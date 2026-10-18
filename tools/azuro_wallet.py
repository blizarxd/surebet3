"""
Wallet del ejecutor de Azuro (Polygon): saldos y permiso del relayer. Corre en el VPS con la clave del .env
(AZURO_PRIVATE_KEY, o POLY_PRIVATE_KEY si es la misma wallet). Nunca imprime la clave.

  .venv/bin/python tools/azuro_wallet.py                 # dirección, POL (gas), USDT y cuánto puede usar el relayer
  .venv/bin/python tools/azuro_wallet.py --approve 300   # permite al relayer de Azuro usar hasta 300 USDT (gasta ~0,01 POL)

El permiso es acotado a propósito (no "infinito"): si algún día hay un problema con el relayer, como mucho arriesga eso.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from surebet.azuro_exec import CHAIN_ID, RELAYER, RPC, USDT, AzuroClient, _word, raw_usdt  # noqa: E402
from surebet.config import Settings  # noqa: E402


async def rpc(client: httpx.AsyncClient, method: str, params: list):
    r = await client.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--approve", type=float, help="USDT que el relayer de Azuro puede usar")
    args = p.parse_args()
    key = Settings().azuro_private_key
    if not key:
        print("No hay AZURO_PRIVATE_KEY ni POLY_PRIVATE_KEY en el .env.")
        return
    az = AzuroClient(key)
    async with httpx.AsyncClient() as client:
        pol = int(await rpc(client, "eth_getBalance", [az.address, "latest"]), 16) / 1e18
        print(f"Wallet: {az.address}")
        print(f"POL (gas): {pol:.4f}")
        print(f"USDT: {await az.usdt_balance(client):,.2f}")
        print(f"Permiso del relayer de Azuro: {await az.allowance(client):,.2f} USDT")
        if args.approve is None:
            return
        if pol < 0.005:
            print("Falta POL para el gas (manda ~0,5 POL a la wallet).")
            return
        from eth_account import Account
        nonce = int(await rpc(client, "eth_getTransactionCount", [az.address, "pending"]), 16)
        gas_price = int(int(await rpc(client, "eth_gasPrice", []), 16) * 1.3)
        tx = {"to": USDT, "value": 0, "gas": 90_000, "gasPrice": gas_price, "nonce": nonce, "chainId": CHAIN_ID,
              "data": "0x095ea7b3" + _word(RELAYER) + f"{raw_usdt(args.approve):064x}"}
        signed = Account.sign_transaction(tx, key)
        tx_hash = await rpc(client, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")])
        print(f"Aprobación enviada: https://polygonscan.com/tx/{tx_hash}")
        for _ in range(40):
            await asyncio.sleep(3)
            rec = await rpc(client, "eth_getTransactionReceipt", [tx_hash])
            if rec:
                print("Confirmada." if rec.get("status") == "0x1" else "La transacción falló.")
                print(f"Permiso del relayer ahora: {await az.allowance(client):,.2f} USDT")
                return
        print("Todavía sin confirmar: revisa el enlace.")


if __name__ == "__main__":
    asyncio.run(main())
