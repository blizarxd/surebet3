"""
Mide si las apuestas de valor detectadas tienen ventaja REAL (CLV, closing line value).

Para cada señal cuyo partido ya empezó, toma el precio de Polymarket justo antes del
inicio (el "cierre", el precio más informado) y calcula:

    CLV = cuota_que_tomaste × probabilidad_de_cierre − 1

Si en cientos de apuestas el CLV medio es positivo, la ventaja es real. Si es ~0 o
negativo, el sistema solo encuentra ruido y no conviene apostar dinero.

    python clv.py              # informe en pantalla
    python clv.py --telegram   # y además lo manda a tu Telegram
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import statistics
import sys
import time
from contextlib import redirect_stdout

import httpx

from surebet.alerts import send_telegram
from surebet.config import Settings

CLOB = "https://clob.polymarket.com"


async def closing_prob(client: httpx.AsyncClient, token: str, start_ts: float) -> float | None:
    r = await client.get(f"{CLOB}/prices-history", params={
        "market": token, "startTs": int(start_ts - 3 * 3600), "endTs": int(start_ts), "fidelity": 1})
    if r.status_code != 200:
        return None
    pts = [h for h in r.json().get("history") or [] if h.get("t", 0) <= start_ts]
    return float(pts[-1]["p"]) if pts else None


async def build_report(s: Settings) -> str:
    """Actualiza el CLV de las señales cuyo partido ya empezó y devuelve el informe como texto."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        await report(s)
    return buf.getvalue()


async def main() -> None:
    s = Settings()
    text = await build_report(s)
    print(text, end="")
    if "--telegram" in sys.argv:
        async with httpx.AsyncClient(timeout=20) as client:
            await send_telegram(client, s.telegram_token, s.telegram_chat_id, "📈 Informe CLV\n" + text)


async def report(s: Settings, keys: list[str] | None = None) -> None:
    """`keys`: solo esas señales (la cuenta de valor, cada hora) y sin imprimir el informe."""
    conn = sqlite3.connect(s.db_path, timeout=30)
    rows = conn.execute(
        "SELECT key, start_ts, covers, odds, poly_tokens FROM value_bets "
        "WHERE clv_pct IS NULL AND start_ts IS NOT NULL AND start_ts < ?", (time.time() - 120,)).fetchall()
    if keys is not None:
        wanted = set(keys)
        rows = [r for r in rows if r[0] in wanted]
    updates = []  # se escriben al final: no dejar la base bloqueada mientras se consulta a Polymarket
    async with httpx.AsyncClient(timeout=20) as client:
        for key, start_ts, covers, odds, tokens_json in rows:
            tokens = json.loads(tokens_json or "{}")
            probs = {o: await closing_prob(client, t, start_ts) for o, t in tokens.items()}
            if not probs or any(p is None for p in probs.values()):
                continue
            total = sum(probs.values())
            p_close = sum(probs[o] for o in covers) / total
            updates.append((p_close, 100 * (odds * p_close - 1), key))
    conn.executemany("UPDATE value_bets SET close_prob=?, clv_pct=? WHERE key=?", updates)
    conn.commit()
    if keys is not None:
        conn.close()
        return

    done = conn.execute("SELECT source, ev_pct, clv_pct FROM value_bets WHERE clv_pct IS NOT NULL").fetchall()
    pending = conn.execute("SELECT COUNT(*) FROM value_bets WHERE clv_pct IS NULL").fetchone()[0]
    print(f"Señales evaluadas: {len(done)}  |  pendientes (partido no empezado o sin datos): {pending}")
    if not done:
        print("Aún no hay partidos terminados para medir. Deja el escáner corriendo unos días.")
        return
    clvs = [r[2] for r in done]
    print(f"CLV medio: {statistics.mean(clvs):+.2f}%  |  mediana: {statistics.median(clvs):+.2f}%  |  "
          f"positivas: {100 * sum(c > 0 for c in clvs) / len(clvs):.0f}%")
    print(f"EV medio estimado al detectar: {statistics.mean(r[1] for r in done):+.2f}%")
    by_src: dict[str, list[float]] = {}
    for src, _ev, clv in done:
        by_src.setdefault(src, []).append(clv)
    for src, vals in sorted(by_src.items(), key=lambda kv: -len(kv[1])):
        print(f"  {src:12s} n={len(vals):4d}  CLV medio {statistics.mean(vals):+.2f}%")
    if len(done) < 100:
        print("Ojo: con menos de ~100 señales el resultado todavía es ruido estadístico.")


if __name__ == "__main__":
    asyncio.run(main())
