"""
Wallet del ejecutor de Azuro (Polygon): saldos, permiso del relayer y cobro de premios. Corre en el VPS con la clave del
.env (AZURO_PRIVATE_KEY, o POLY_PRIVATE_KEY si es la misma wallet). Nunca imprime la clave.

  .venv/bin/python tools/azuro_wallet.py                 # dirección, POL (gas), USDT, permiso y premios por cobrar
  .venv/bin/python tools/azuro_wallet.py --approve 300   # permite al relayer de Azuro usar hasta 300 USDT (gasta ~0,01 POL)
  .venv/bin/python tools/azuro_wallet.py --claim         # cobra ya las ganadas/anuladas (el ejecutor lo hace solo cada 15 min)

El permiso es acotado a propósito (no "infinito"): si algún día hay un problema con el relayer, como mucho arriesga eso.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from surebet.azuro_exec import RELAYER, USDT, AzuroClient, _word, raw_usdt  # noqa: E402
from surebet.config import Settings  # noqa: E402


async def wait_receipt(az: AzuroClient, client: httpx.AsyncClient, tx_hash: str) -> bool:
    for _ in range(40):
        await asyncio.sleep(3)
        rec = await az._rpc(client, "eth_getTransactionReceipt", [tx_hash])
        if rec:
            return rec.get("status") == "0x1"
    return False


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--approve", type=float, help="USDT que el relayer de Azuro puede usar")
    p.add_argument("--claim", action="store_true", help="cobrar ya las apuestas ganadas o anuladas")
    args = p.parse_args()
    key = Settings().azuro_private_key
    if not key:
        print("No hay AZURO_PRIVATE_KEY ni POLY_PRIVATE_KEY en el .env.")
        return
    az = AzuroClient(key)
    if not az.address:
        print(az.error or "La clave no sirve.")
        return
    async with httpx.AsyncClient() as client:
        pol = await az.pol_balance(client)
        bets = await az.redeemable(client)
        print(f"Wallet: {az.address}")
        print(f"POL (gas): {pol:.4f}")
        print(f"USDT: {await az.usdt_balance(client):,.2f}")
        print(f"Permiso del relayer de Azuro: {await az.allowance(client):,.2f} USDT")
        print(f"Por cobrar en Azuro: {len(bets)} apuestas, ${sum(b['payout'] for b in bets):,.2f}")
        if (args.approve is not None or args.claim) and pol < 0.005:
            print("Falta POL para el gas (manda ~0,5 POL a la wallet).")
            return
        if args.approve is not None:
            tx = await az.send_tx(client, USDT, "0x095ea7b3" + _word(RELAYER) + f"{raw_usdt(args.approve):064x}", gas=90_000)
            print(f"Aprobación enviada: https://polygonscan.com/tx/{tx}")
            print("Confirmada." if await wait_receipt(az, client, tx) else "Sin confirmar o falló: revisa el enlace.")
            print(f"Permiso del relayer ahora: {await az.allowance(client):,.2f} USDT")
        if args.claim and bets:
            groups: dict[tuple, list] = {}
            for b in bets:
                groups.setdefault((b["core"], b["lp"]), []).append(b["bet_id"])
            for (core, lp), ids in groups.items():
                tx = await az.claim(client, core, lp, ids[:20])
                print(f"Cobro de {len(ids[:20])} apuestas enviado: https://polygonscan.com/tx/{tx}")
                print("Confirmado." if await wait_receipt(az, client, tx) else "Sin confirmar o falló: revisa el enlace.")


if __name__ == "__main__":
    asyncio.run(main())
