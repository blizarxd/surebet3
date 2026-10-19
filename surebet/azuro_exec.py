"""
Apuestas REALES en Azuro (Polygon, USDT) por su API: firma EIP-712 + relayer, sin gas por apuesta.
Verificado contra el paquete oficial @azuro-org/toolkit 7.2.0 (getBetTypedData / createBet / getBet / getBetFee) y las
direcciones de V3 en Polygon (gem.azuro.org/hub/blockchains/deployment-addresses):

  1. GET  /bet/gas-info?environment=PolygonUSDT   → relayerFeeAmount (lo que cobra el relayer, en USDT)
  2. POST /bet/calculation                        → cuota y maxBet del momento
  3. firma EIP-712 de ClientBetData (dominio "Live Betting" 1.0.0, chainId 137, verifyingContract = core)
  4. POST /bet/orders/ordinar                     → id de la orden
  5. GET  /bet/orders/{id}                        → state: Created / Placed / Sent → Accepted | Rejected | Canceled

Antes de la primera apuesta la wallet necesita USDT en Polygon y haber aprobado USDT al relayer (una vez, con un poco de
POL para el gas): tools/azuro_wallet.py. Los premios (y las anuladas) no llegan solos: se cobran con
LP.withdrawPayouts(core, ids) (también con POL); las cobrables salen del subgraph de apuestas (isRedeemable).
La clave vive solo en el .env del VPS (AZURO_PRIVATE_KEY, o POLY_PRIVATE_KEY si es la misma wallet): nunca se escribe
en logs ni en la base.
"""

from __future__ import annotations

import asyncio
import time

import httpx

API = "https://api.onchainfeed.org/api/v1/public"
ENV = "PolygonUSDT"
CHAIN_ID = 137
CORE = "0xF9548Be470A4e130c90ceA8b179FCD66D2972AC7"
RELAYER = "0x8dA05c0021e6b35865FDC959c54dCeF3A4AbBa9d"
LP = "0x0FA7FB5407eA971694652E6E16C12A52625DE1b8"
USDT = "0xc2132D05D31c914a87C6611C10748AEb04B58e8F"
USDT_DECIMALS = 6
ODDS_DECIMALS = 12
RPC = "https://polygon-rpc.com"
BETS_GRAPHQL = "https://thegraph.onchainfeed.org/subgraphs/name/azuro-protocol/azuro-api-polygon-v3"
WITHDRAW_PAYOUTS = "0xe8e3e87b"   # keccak("withdrawPayouts(address,uint256[])")[:4] (ABI del LP en el toolkit)
ZERO = "0x" + "0" * 40
ACCEPTED = {"Accepted", "Settled"}
FAILED = {"Rejected", "Canceled", "CancelFailed"}
ORDER_TTL_S = 300            # la orden firmada vale 5 min: es una apuesta al precio de ahora, no una orden que espera

CLIENT_DATA_TYPES = [
    {"name": "attention", "type": "string"},
    {"name": "affiliate", "type": "address"},
    {"name": "core", "type": "address"},
    {"name": "expiresAt", "type": "uint256"},
    {"name": "chainId", "type": "uint256"},
    {"name": "relayerFeeAmount", "type": "uint256"},
    {"name": "isFeeSponsored", "type": "bool"},
    {"name": "isBetSponsored", "type": "bool"},
    {"name": "isSponsoredBetReturnable", "type": "bool"},
]
BET_TYPES = {
    "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                     {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
    "ClientBetData": [{"name": "clientData", "type": "ClientData"}, {"name": "bets", "type": "SubBet[]"}],
    "ClientData": CLIENT_DATA_TYPES,
    "SubBet": [{"name": "conditionId", "type": "uint256"}, {"name": "outcomeId", "type": "uint128"},
               {"name": "minOdds", "type": "uint64"}, {"name": "amount", "type": "uint128"},
               {"name": "nonce", "type": "uint256"}],
}


def raw_odds(odds: float) -> int:
    return int(round(odds * 10 ** ODDS_DECIMALS))


def raw_usdt(amount: float) -> int:
    return int(round(amount * 10 ** USDT_DECIMALS))


def typed_data(client_data: dict, bet: dict) -> dict:
    """El mensaje EIP-712 que firma el apostador (mismo que getBetTypedData del toolkit)."""
    return {
        "types": BET_TYPES,
        "primaryType": "ClientBetData",
        "domain": {"name": "Live Betting", "version": "1.0.0", "chainId": int(client_data["chainId"]),
                   "verifyingContract": client_data["core"]},
        "message": {
            "clientData": {**client_data, "expiresAt": int(client_data["expiresAt"]),
                           "chainId": int(client_data["chainId"]),
                           "relayerFeeAmount": int(client_data["relayerFeeAmount"])},
            "bets": [{"conditionId": int(bet["conditionId"]), "outcomeId": int(bet["outcomeId"]),
                      "minOdds": int(bet["minOdds"]), "amount": int(bet["amount"]), "nonce": int(bet["nonce"])}],
        },
    }


def _word(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


class AzuroClient:
    def __init__(self, private_key: str = "", affiliate: str = ZERO, api: str = API, rpc: str = RPC) -> None:
        self._pk = private_key
        self.affiliate, self.api, self.rpc = affiliate or ZERO, api, rpc
        self._account = None
        self.error = ""
        if private_key:
            try:
                from eth_account import Account   # viene con py-clob-client
                self._account = Account.from_key(private_key)
            except ImportError:
                self.error = "falta instalar py-clob-client (eth-account) en el VPS"
            except Exception:
                self.error = "la clave de Azuro no es válida"

    @property
    def address(self) -> str | None:
        return self._account.address if self._account else None

    # ------------------------------------------------------------------ lectura
    async def fee(self, client: httpx.AsyncClient) -> int:
        """Comisión del relayer en unidades de USDT (6 decimales)."""
        r = await client.get(f"{self.api}/bet/gas-info", params={"environment": ENV}, timeout=10)
        r.raise_for_status()
        data = r.json()
        data = data[0] if isinstance(data, list) else data
        raw = str(data.get("relayerFeeAmount") or "0")
        return int(raw) if raw.isdigit() else raw_usdt(float(raw))

    async def quote(self, client: httpx.AsyncClient, condition_id: str, outcome_id: str) -> tuple[float | None, float]:
        """(cuota de ahora, máximo que acepta) de una selección: la cuota de la condición activa y el maxBet."""
        r = await client.post(f"{self.api}/market-manager/condition-batch", timeout=10,
                              json={"conditionIds": [str(condition_id)], "environment": ENV})
        cond = ((r.json() or {}).get("conditions") or [None])[0] if r.status_code in (200, 201) else None
        out = next((x for x in (cond or {}).get("outcomes") or [] if str(x.get("outcomeId")) == str(outcome_id)), None)
        if not cond or cond.get("state") != "Active" or not out or out.get("state") not in (None, "Active"):
            return None, 0.0
        r = await client.post(f"{self.api}/bet/calculation", timeout=10, json={
            "environment": ENV, "bets": [{"conditionId": str(condition_id), "outcomeId": int(outcome_id)}],
            **({"bettor": self.address.lower()} if self.address else {})})
        resp = ((r.json() or {}).get("response") or {}) if r.status_code in (200, 201) else {}
        return (float(out.get("odds") or 0) or None), float(resp.get("maxBet") or 0)

    async def _call(self, client: httpx.AsyncClient, to: str, data: str) -> int:
        r = await client.post(self.rpc, timeout=10, json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                                          "params": [{"to": to, "data": data}, "latest"]})
        return int((r.json() or {}).get("result") or "0x0", 16)

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: list):
        r = await client.post(self.rpc, timeout=20, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        data = r.json()
        if "error" in data:
            raise RuntimeError(str(data["error"])[:200])
        return data["result"]

    async def pol_balance(self, client: httpx.AsyncClient) -> float:
        return int(await self._rpc(client, "eth_getBalance", [self.address, "latest"]), 16) / 1e18

    async def send_tx(self, client: httpx.AsyncClient, to: str, data: str, gas: int | None = None) -> str:
        """Firma y envía una transacción en Polygon (paga gas en POL). Devuelve el hash."""
        from eth_account import Account
        from eth_utils import to_checksum_address    # eth-account rechaza direcciones en minúscula como destino
        to = to_checksum_address(to)
        nonce = int(await self._rpc(client, "eth_getTransactionCount", [self.address, "pending"]), 16)
        gas_price = int(int(await self._rpc(client, "eth_gasPrice", []), 16) * 1.3)
        if gas is None:
            try:
                gas = int(int(await self._rpc(client, "eth_estimateGas", [{"from": self.address, "to": to, "data": data}]),
                              16) * 1.3)
            except RuntimeError:
                gas = 400_000
        tx = {"to": to, "value": 0, "gas": gas, "gasPrice": gas_price, "nonce": nonce, "chainId": CHAIN_ID, "data": data}
        signed = Account.sign_transaction(tx, self._pk)
        return await self._rpc(client, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")])

    async def redeemable(self, client: httpx.AsyncClient) -> list[dict]:
        """Apuestas de esta wallet listas para cobrar (ganadas o anuladas): {bet_id, core, lp, payout}."""
        q = ("query($a: String!) { v3Bets(first: 50, where: {actor: $a, isRedeemable: true, isCashedOut: false}) "
             "{ betId payout amount core { address liquidityPool { address } } } }")
        r = await client.post(BETS_GRAPHQL, timeout=20, json={"query": q, "variables": {"a": self.address.lower()}})
        bets = ((r.json() or {}).get("data") or {}).get("v3Bets") or []
        return [{"bet_id": int(b["betId"]), "core": b["core"]["address"], "lp": b["core"]["liquidityPool"]["address"],
                 "payout": float(b.get("payout") or b.get("amount") or 0)} for b in bets]

    @staticmethod
    def withdraw_data(core: str, bet_ids: list[int]) -> str:
        """LP.withdrawPayouts(core, ids): selector + core + puntero al arreglo + largo + ids."""
        return (WITHDRAW_PAYOUTS + _word(core) + f"{64:064x}" + f"{len(bet_ids):064x}"
                + "".join(f"{i:064x}" for i in bet_ids))

    async def claim(self, client: httpx.AsyncClient, core: str, lp: str, bet_ids: list[int]) -> str:
        return await self.send_tx(client, lp, self.withdraw_data(core, bet_ids))

    async def usdt_balance(self, client: httpx.AsyncClient) -> float:
        return await self._call(client, USDT, "0x70a08231" + _word(self.address)) / 10 ** USDT_DECIMALS

    async def allowance(self, client: httpx.AsyncClient) -> float:
        """USDT que el relayer puede usar de esta wallet (tiene que alcanzar para la apuesta + comisión)."""
        return await self._call(client, USDT, "0xdd62ed3e" + _word(self.address) + _word(RELAYER)) / 10 ** USDT_DECIMALS

    # ------------------------------------------------------------------ apuesta
    def build(self, condition_id: str, outcome_id: str, stake: float, min_odds: float, fee_raw: int,
              now: float | None = None) -> dict:
        """Cuerpo del POST /bet/orders/ordinar, firmado si hay clave (sin clave: el mismo cuerpo sin firma)."""
        now = now or time.time()
        bettor = (self.address or ZERO).lower()
        client_data = {"attention": "", "affiliate": self.affiliate, "core": CORE, "expiresAt": int(now + ORDER_TTL_S),
                       "chainId": CHAIN_ID, "relayerFeeAmount": str(fee_raw), "isFeeSponsored": False,
                       "isBetSponsored": False, "isSponsoredBetReturnable": False}
        bet = {"conditionId": str(condition_id), "outcomeId": int(outcome_id), "minOdds": str(raw_odds(min_odds)),
               "amount": str(raw_usdt(stake)), "nonce": str(int(now * 1000))}
        signature = None
        if self._account:
            signed = self._account.sign_typed_data(full_message=typed_data(client_data, bet))
            signature = "0x" + signed.signature.hex().removeprefix("0x")
        return {"environment": ENV, "bettor": bettor, "betOwner": bettor,
                "clientBetData": {"clientData": client_data, "bet": bet}, "bettorSignature": signature}

    async def place(self, client: httpx.AsyncClient, body: dict) -> dict:
        """Envía la orden firmada. Devuelve la respuesta de Azuro (trae el id). Lanza si Azuro la rechaza."""
        if not body.get("bettorSignature"):
            raise RuntimeError("sin clave de Azuro: no se puede firmar")
        r = await client.post(f"{self.api}/bet/orders/ordinar", json=body, timeout=15,
                              headers={"Accept": "application/json", "Content-Type": "application/json"})
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(str((data or {}).get("errorMessage") or (data or {}).get("error") or r.status_code)[:200])
        return data

    async def wait(self, client: httpx.AsyncClient, order_id: str, timeout_s: float = 90, every_s: float = 3) -> dict:
        """Espera a que la orden quede aceptada o rechazada en la cadena. Devuelve el último estado leído."""
        t0, last = time.time(), {}
        while time.time() - t0 < timeout_s:
            try:
                r = await client.get(f"{self.api}/bet/orders/{order_id}", timeout=10)
                if r.status_code == 200:
                    last = r.json() or {}
                    if last.get("state") in ACCEPTED | FAILED:
                        return last
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(every_s)
        return {**last, "state": last.get("state") or "Sin respuesta", "timeout": True}
