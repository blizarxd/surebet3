# surebet3 · ESTADO (leer esto primero en una conversación nueva)

Actualizado: 19/10/2026. Todo es **papel** (simulación): ningún dinero real ni orden enviada.

## Dónde está todo
- **Panel:** http://65.21.243.40/surebet/ (VPS Hetzner, `root@65.21.243.40`, `/opt/apps/surebet3`, servicio `surebet3`).
- **Repo privado:** github.com/blizarxd/surebet3 (commits como blizarxd / blizar9@gmail.com).
- **Memoria larga con todas las decisiones:** `memory/surebet-project.md` (leerla solo si hace falta un detalle).
- **Desplegar (Windows, desde `surebet3/`):**
  1. `python -m pytest -q tests > $SP/pt.txt 2>&1` y NO desplegar si el archivo contiene " failed".
  2. `tar czf - --exclude=__pycache__ scan.py surebet static tools | ssh root@65.21.243.40 "tar xzf - -C /opt/apps/surebet3 && systemctl restart surebet3"`
- Python del VPS: `/opt/apps/surebet3/.venv/bin/python` (el del sistema no tiene httpx).
- Pruebas locales del escáner siempre con `--quiet` (sin Telegram).

## Qué es
Escáner de surebets y valor: Polymarket vs casas blandas. Fuentes: Polymarket, 1xBet (feed gratis 1xbet-col.com), SX Bet,
Cloudbet, Kalshi, Azuro (Polygon), OddsPapi (Rushbet, BetPlay, Pinnacle), The Odds API.
Usuario: Cúcuta, Colombia; apuesta 1xBet a mano y el bot hace lo de Polymarket. Habla informal, prefiere que se construya directo.

## Cuentas en papel (19/10, 9,4 días)
| Cuenta | Ganancia | Nota |
|---|---|---|
| Semi-manual (surebets 1xBet+Poly, $1.000, ≤6 h) | +$162 | 180 ganadas, 0 perdidas, neto +0,95 %/op |
| Automático (cuenta $3.000, tope $1.500, 90 % del libro, precio real) | +$251 | sin CS2/Dota2 bajó a ~5 ops/día |
| Valor oro simple ($1.000) | +$1.009 | ROI 11 % con suerte; CLV oro +3,05 % (641), 78 % a favor |
| Valor + red (🛟) | +$597 | sin red sería +$930; caída máx. 5,9 % |
Automático por casa: Azuro+Poly +$57 (38 ops), Kalshi 6 ops +$109 (2 grandes), Poly+SX +$38, Azuro+SX +$22.
Con tope $30 por surebet: ~$1–2/día; $300: ~$14/día. 8 de 9 anuladas eran CS2.

## Reglas y decisiones que NO hay que repetir
- **Anuladas:** Polymarket paga $0,50/share en 50/50 (la casa devuelve la apuesta) → `void_payout()`. Casi todas son esports CS2/Dota2.
- **Descartado con datos:** ventana ≤2 h (la prueba estuvo dañada por un bug), automático en vivo (1 de 38), recompensas de liquidez
  (`LP_ENABLED=0`), maker automático (`MAKER_AUTO_ENABLED=0`, −$26), Kalshi↔Polymarket fuera de deportes (sin ventaja), Limitless
  (copia a Polymarket), Overtime (API con clave aprobada y prohíbe arbitraje/bots; sus cuotas vienen de Pinnacle), Pinnacle API directa (cerrada).
- **Seguridad (no cambiar):** NO VPN/geoblock/anti-bot/captcha, NO bot sobre la web de 1xBet, NO multicuentas en una casa.
  Claves SOLO en `.env` del VPS (el usuario las pone; nunca en el chat). Nunca ráfagas de peticiones a 1xBet.
- **Lección de código:** nunca dejar una escritura sqlite abierta durante un `await` (commit antes). Strings con `\n` en heredocs
  de Python se rompen: usar Write/Edit. `pytest | tail && deploy` esconde fallas.

- **CS2 y Dota2 (18/10, decidido):** fuera del automático; en la cuenta semi-manual van a medición (observación). Telegram
  los sigue avisando con ⚠ (a mano, con cuidado). Lista en `safety.OBSERVE_SPORTS`.
- **Tiers de EV (18/10, decidido):** NO subir montos con EV > 7 % hasta tener más datos (suelen ser mapeo malo o cuota congelada).
- **Semáforo de riesgo** (`surebet/semaforo.py`, arriba en el panel): 🟢 caída < 10 % y CLV a favor · 🟡 caída 10–20 % o CLV
  negativo 3 días · 🔴 caída > 20 %, cuenta bloqueada o API caída 5 min. En rojo pausa solo entradas NUEVAS (avisos y papel);
  las coberturas ✅/🛟 siguen. Caída > 20 % queda pausada hasta `/reanudar`; API se levanta sola. Telegram: `/semaforo`,
  `/bloqueada <casa>`, `/reanudar`.
- **Cubrir en vivo al minuto 15** (`surebet/livehedge.py`, pestaña Valor + red): backtest y sombra sobre los picks de valor + red;
  compara 7 formas de cubrir (esperado sin suerte y real). Solo mide. **Resultado 18/10 (385 picks): NO da más plata.** Del cierre
  al 15' el precio se mueve +0,4 puntos de media; cubrir en vivo como taker cuesta ~5 % del monto (D −0,2 %, C −1,2 % esperado);
  red actual +2,1 %; red + minuto 15 si vamos ganando +2,5 % (+$19 en 385 picks, ruido). Se deja la red como está.
- **Valor automático en sombra** (`surebet/sxshadow.py`, pestaña 🤖): cada señal oro se prueba en SX, Azuro y Cloudbet con el precio
  leído al ejecutar (5 s), al monto. Motivo: el escáner refresca SX cada 5 min (1–6 h antes) y en el automático 15 de 20 intentos con
  SX fallaron por cuota real ~5 % más baja. Candidata a reemplazar a 1xBet: **Azuro** (CLV oro +5,6 a +6,6 %, ~16 señales/día).
  Aprobar una casa si ≥ 100 llenadas, ≥ 50 % llenadas y CLV real ≥ +1,5 %.
- **Automático por casa (18/10, 90 cerradas):** las 10 mejores hacen el 80 % de la ganancia (montos de $400–1.400); Kalshi: 6 ops,
  +$110 (2 de $940–1.060 con margen 4–5,6 %). Con tope $30 por surebet: +$6,83 (+$15,88 sin CS2/Dota2, ~$2/día); $100: ~$6/día;
  $300: ~$15/día. 8 de las 9 anuladas eran CS2. Desglose en la pestaña Automático.

- **Dónde está el oro (19/10):** valor PREPARTIDO de FÚTBOL. CLV oro fútbol mediana +2,8 % (188, 87 % a favor); básquet y
  tenis medianas +2,2 a +2,5 % pero medias negativas (~55 % a favor). CS2/Dota2 fuera por las SUREBETS: CS2 15,6 % anuladas
  (neto +0,24 % vs fútbol +1,17 %), Dota2 neto −1,27 %; como valor CS2 da CLV +3,5 % pero son pocas (13).
- **Sombra 1er día (19/10):** las señales que nacen en la casa con API se llenan (Azuro 3/3, SX 3/11, Cloudbet 1/1); las de
  1xBet probadas en SX/Azuro casi nunca (0/26: ahí no hay valor). 5 de 8 llenadas eran CS2 → el ejecutor ahora excluye CS2/Dota2.
- **Ejecutor REAL Azuro + Polymarket** (`surebet/executor.py`, `surebet/azuro_exec.py`; tarjeta arriba en la pestaña Automático):
  (A) surebets Azuro + Polymarket confirmadas con precio real: primero Azuro, si la acepta Polymarket FOK con shares enteras;
  pata suelta → reintenta cada minuto, cubre si pierde ≤ 1 %, si no queda como valor y avisa. (B) valor + red en Azuro (lo que
  la sombra da por llenado): Azuro + red 🛟. Modo `AUTO_EXEC=dry` por defecto (lee precios reales, no firma ni envía).
  Firma EIP-712 verificada contra @azuro-org/toolkit 7.2.0. Monto: `AUTO_AZURO_STAKE=30` (decidido 19/10: $30 en Azuro, Polymarket en proporción). Topes: `AUTO_MAX_PER_OP=100`,
  `AUTO_MAX_DAILY=600`, `AUTO_MAX_OPEN=8`. Polymarket del usuario: cuenta con email → `POLY_SIGNATURE_TYPE=1` + `POLY_FUNDER`.
  Interruptores: `AUTO_VALUE_NET=0` = valor en Azuro SIN red; `AUTO_ARB=0` = sin surebets Azuro+Poly (con los dos en 0 solo
  hace falta saldo en Azuro y no exige POLY_LIVE). Cobra solo las ganadas/anuladas cada 15 min (LP.withdrawPayouts, gas POL).
  Capital solo Azuro sin red con $30: ~$300 (pico ~$270 cobrando al instante) + ~0,5 POL.
  **Para pasar a live:** wallet nueva solo para el bot (Polygon) con USDT (Azuro), USDC en Polymarket y ~0,5 POL; el usuario
  pone la clave en el .env (`POLY_PRIVATE_KEY`, y `POLY_FUNDER`/`POLY_SIGNATURE_TYPE` si es cuenta web de Polymarket);
  `tools/azuro_wallet.py --approve 300`; `POLY_LIVE=1`; `AUTO_EXEC=live`. Cobrar premios de Azuro: LP.withdrawPayout (pendiente
  de herramienta; se puede desde bookmaker.xyz con la misma wallet).
- **Backtest valor + red en Azuro (18/10, 33 picks en 2,3 días):** regla actual (~$13 Azuro + ~$17 red) esperado +$11,5 con red /
  +$21 sin red (real +$20 / +$101, suerte); con $30 en Azuro: esperado +$27 con red (~$12/día) / +$47 sin red. CLV Azuro: mediana
  +3,6 % (la media +6,5 % la inflan 2 casos raros). 10 de las 21 redes llenas ya eran surebet al entrar.

## Banderas de `.env` (VPS) que importan
`AUTO_MAX_STAKE=1500`, `ALERT_ARB_STAKE=50`, `PAPER_REAL_MAX_HOURS=6`, `MY_BOOKS=...,kalshi,azuro`,
`ODDSPAPI_BOOKMAKERS=rushbet.co,betplay,pinnacle`, `AUTOVIVO_ENABLED=0`, `LP_ENABLED=0`, `MAKER_AUTO_ENABLED=0`.

## Pendiente
1. **Sombra de SX** (pestaña 🤖): revisar en 1–2 semanas si cumple para probar en real con montos chicos.
2. **Pinnacle en sombra** (pestaña Valor): esperar 1–2 semanas; ver si su EV acierta más que Polymarket y cuántas señales tienen a 1xBet a ≤1,5 %.
3. Ver la hipótesis "sin CS2 ni Dota2" en el panel (antes/después) en unos días.
4. **Usuario:** 25 toques de botones (✅ ❌ 🛟) para el semáforo de la fase 1 (lleva 5/30); luego Fase 1 real a $50 por surebet.
5. **Antes de real automático:** programar el envío de órdenes (Polymarket, SX, Cloudbet, Kalshi, Azuro), protección de pata suelta, topes
   diarios, pausa por Telegram. Capital ~$3.000-4.000 repartido; empezar con $10-20 por surebet.
