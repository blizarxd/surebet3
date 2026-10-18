# surebet3 · ESTADO (leer esto primero en una conversación nueva)

Actualizado: 18/10/2026 (tarde). Todo es **papel** (simulación): ningún dinero real ni orden enviada.

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

## Cuentas en papel (18/10, 8,5 días)
| Cuenta | Ganancia | Nota |
|---|---|---|
| Semi-manual (surebets 1xBet+Poly, $100, ≤6 h) | +$137 | 0 perdidas |
| Automático (cuenta $3.000, tope $1.500, 90 % del libro, precio real) | +$221 | 1 perdida de −$0,20 |
| Valor oro simple | +$888 | volátil; lo que prueba la ventaja es el CLV |
| Valor + red (🛟 orden límite maker en Polymarket) | +$589 | baja caída máx. de 13,5 % a 6,3 % |
Valor+red por casa: SX +$233 (CLV +5,3 %), Azuro +$43 (CLV +4,0 %), 1xBet +$298 (CLV +2,6 %).

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
- **Valor automático en SX en sombra** (`surebet/sxshadow.py`, pestaña 🤖): cada señal oro se prueba contra el libro de SX leído al
  ejecutar (5 s), al monto. Motivo: el escáner refresca SX cada 5 min (1–6 h antes) y en el automático 15 de 20 intentos con SX
  fallaron por cuota real ~5 % más baja. Aprobar si ≥ 100 llenadas, ≥ 50 % llenadas y CLV real ≥ +1,5 %.

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
