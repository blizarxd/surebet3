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
- **Más picks de Azuro (19/10):** Azuro ahora lee también más/menos goles (1,5–4,5), 1X2 del 1er tiempo y más/menos 1T
  (0,5–2,5): ~3× más precios de Azuro para comparar (límites de esos mercados solo para partidos a < 8 h). Además el nivel
  "amplio" (EV 1,5–2 %, `safety.wide_value`) se mide en la sombra aparte; el bot real y los avisos siguen solo con oro.
  Pestaña 🟦 Azuro: todo el valor de Azuro a $30 (día por día, con/sin red, por mercado, precio real, ejecutor).
- **Mini prueba real (19/10, decidida):** solo Azuro, valor SIN red, $5 por pick, ~$50 USDT + ~1 POL en una wallet NUEVA
  (clave en `AZURO_PRIVATE_KEY`, la pone el usuario). VPS ya con `AUTO_VALUE_NET=0`, `AUTO_ARB=0`, `AUTO_AZURO_STAKE=5`,
  `AUTO_MAX_DAILY=100` (respaldo del .env en /root/surebet3.env.bak3/bak4). **19/10: wallet del bot 0xCeB5…E900 con clave en el
  .env (la puso el usuario con tools/set_azuro_key.py), 1 POL, permiso de 100 USDT aprobado. Falta: que lleguen los $50 USDT
  y que el usuario diga que arranque → `AUTO_EXEC=live`.** (La 1ª wallet 0xB55a… quedó descartada: su clave se pegó en el chat.) Azuro hoy: comisión del relayer $0, apuesta mínima $1, máximo con valor ~$67–4.380 (mediana ~$100).
  **Para pasar a live:** wallet nueva solo para el bot (Polygon) con USDT (Azuro), USDC en Polymarket y ~0,5 POL; el usuario
  pone la clave en el .env (`POLY_PRIVATE_KEY`, y `POLY_FUNDER`/`POLY_SIGNATURE_TYPE` si es cuenta web de Polymarket);
  `tools/azuro_wallet.py --approve 300`; `POLY_LIVE=1`; `AUTO_EXEC=live`. Cobrar premios de Azuro: LP.withdrawPayout (pendiente
  de herramienta; se puede desde bookmaker.xyz con la misma wallet).
- **Corte 19/10 noche (datos para arrancar real):** sombra de Azuro (desde 18/10 13:11): sin CS2/Dota2 solo 3 señales
  nacidas en Azuro, 2 llenadas (Getafe, Fram; ambas perdidas, CLV −0,1 % y +12 %); Braga Menos 1,5 rechazada (escáner 3,31,
  real 2,97: la cuenta de valor en papel la contó igual → el papel de Azuro exagera; la verdad es la sombra/Real vs papel).
  CLV de Azuro por horas antes del inicio (todas las señales): 0–1 h +3,0 % (84, 82 % a favor), 1–3 h +2,2 %, **3–6 h ~0 %
  (33, 52 %)** → propuesta: Azuro solo ≤ 3 h. EV 1–2 % en Azuro tiene CLV parecido a EV 2–5 % (1–1,5: +3,4 %, 31). EV 5–7 %
  malo (−6,9 %, 7). Valor + red cuesta ~40 % del esperado (exp. ROI 2,2 % con red vs 3,4 % sin red, 519 picks).
  **Decidido 19/10: Azuro automático solo ≤ 3 h** (`safety.AZURO_MAX_HOURS`; la sombra espera y prueba Azuro al entrar a 3 h;
  el ejecutor rechaza valor a > 3 h). Dry de hoy: el tope diario en dry quedó lleno por filas de $30 de antes del cambio (en live solo cuentan las live).
  Wallet 0xCeB5…E900: USDT 0,00 (falta el depósito), 0,98 POL, permiso 100 USDT.
- **Valor 1xBet a mano con botones (20/10)** (`surebet/manualvalor.py`, sección 🖐 en Real vs papel): cada pick de 1xBet de la
  cuenta de valor (~47/día) llega por Telegram como aviso limpio (cuota, mínima con EV +1 %, $30 fijos) con botones «✅ la puse
  a X» (3 cuotas) y «❌ no estaba»; solo en `MANUAL_VALUE_HOURS` (8-20, hora Colombia) y con /estoy (/salgo pausa, /hoy resume).
  Fuera de horario se anota igual. Reemplaza el aviso viejo de valor de 1xBet (sin red). Backtest 10 días a $30 por hora:
  8–20 h ≈ 29 picks/día, esperado ~$27/día (real +$986); todo el día 47/día, ~$44/día. Hora fuerte: 13 h (6/día, Europa).
- **EJECUTOR EN REAL (20/10):** el usuario recargó $35,98 USDT a 0xCeB5…E900 y puso `AUTO_EXEC=live` (respaldo /root/surebet3.env.bak5).
  Reglas de la prueba: solo Azuro, valor oro, $5 por pick, ≤ 3 h, sin red, sin surebets, tope $100/día, sin CS2/Dota2. Se mide
  en la sombra el nivel amplio (EV 1,5–2 %) y el tramo > 3 h (tier 'lejos', solo medición). Plan: unos días a $5; si oro + amplio
  siguen bien (CLV), pasar a oro + amplio. Azuro 19–20/10 sombra, 5 partidos reales sin esports: 1 ganada, 3 perdidas, 1 anulada
  (−$10 a $5), CLV mediano +2 %.
- **Pestaña 📊 Dashboard valor manual (21/10)** (`surebet/valordash.py` + sección en `static/index.html`): el valor oro de 1xBet a $30
  fijos, en vivo (el panel refresca cada 15 s y se redibuja solo al cerrarse un partido): KPIs, curva acumulada, mapa día × franja de 4 h
  (hora Colombia), barras real vs esperado por franja, 🎯 «para apostar ahora» con botón a 1xBet, riesgo (caída máx., rachas, banca),
  6 desgloses (deporte, mercado, cuota, EV, horas antes, día), lista de todas las apuestas (20 por página, orden, CSV), filtros
  (monto $5–100, franja, deporte, mercado, resultado, equipo, «solo las que puse ✅») y un gráfico por día. Versión para compartir sin panel:
  `reports/dashboard_valor_oro.html` (estática, se regenera a mano).
- **Prueba real, primeros resultados (22/10):** 7 apuestas de $5 en Azuro, las 7 perdidas (−$35; wallet 0,98 USDT). CLV positivo en
  6 de 7 (precio bueno; mala suerte). Pero 4 de las 7 eran 2 pares del MISMO partido (Menos 2.5 + Menos 1.5; 1T Brighton + Más 1T 1.5),
  perfectamente correlacionados → en realidad 5 apuestas independientes. Arreglado: el ejecutor ahora hace UNA jugada por partido
  (antes solo bloqueaba el mismo mercado). Pendiente: decidir si recargar y seguir.
- **Duración de las señales de valor (22/10)** (dashboard valor manual, columna «Cuota (duración)» y tabla «Cuánto duran las cuotas»):
  se mide DESDE EL AVISO hasta la última vez que el escáner la vio como valor. «🟢 vigente» mientras siga (no se marca duración: aún se
  puede apostar); «duró X» cuando cambia; «hasta el inicio» si duró hasta el partido. 581 señales: 99 % siguen a los 30 s, 97 % al
  minuto, 95 % a los 2 min, 91 % a los 5, 86 % a los 15, 81 % a la hora; mediana ~1 h 36. Las de 1er tiempo son las rápidas (mediana
  5 min, 80 % a los 5 min); tenis no cambia. (El análisis previo medía desde la 1.ª vez que la cuota existió y salía subestimado.)
- **Alerta de pantalla completa + filtros de cuota (23/10)** (`static/index.html`, pestaña 📊): cuando sale una señal nueva de valor de
  1xBet, en CUALQUIER pestaña del panel salta una pantalla con luces tipo discoteca (copiada del «Señor de la Noche» de
  Desktop/botpolym/webapp/index.html), emoji del deporte, partido, selección, cuota/mínima/EV, cuenta regresiva, 🟢 vigente / ⚪ cambió,
  sonido y botón «🚀 APOSTAR EN 1xBET». Botones para encender/apagar alerta y sonido y «▶ Probar alerta» (se guarda en localStorage).
  Filtros nuevos: cuota mín./máx. y horas antes del partido. Análisis por cuota (12 días): el esperado es ~3 % por apuesta en todos los
  tramos; lo que ganó de más en cuota ≥ 2 (+$1.842 real vs +$355 esperado) es suerte; no filtrar por cuota todavía.
- **1xBet intermitente (23/10):** el 23/10 1xBet empezó a rechazar consultas por ratos (semáforo 🔴 en APIs, se pausan avisos que lo usan).
  Medida (no es esquivar el bloqueo, es pedir menos): `onexbet.REQUEST_GAP_S` 0,3 → 0,6 s y `ONEXBET_LIVE_S=60` (antes 10 s) en el .env
  (respaldo /root/surebet3.env.bak6). Vigilar si bajan los cortes; si siguen, bajar más (prematch 120 s, primer tiempo).
- **Casas para rotar a mano (23/10):** el usuario apuesta desde Venezuela y rota 1xBet, Melbet y 888starz (`MANUAL_HOUSES`) para evitar
  límites. `/casa [nombre]` en Telegram elige la casa de hoy; el aviso la muestra y dice «en Melbet tómala si está a X o más (mira su
  cuota)»; los ✅/❌ se guardan por casa (`manual_value.house`); el dashboard 📊 tiene filtro «Casa donde la puse» y la tabla «🏦 Por
  casa» (puestas, no estaba, % disponible, tu ganancia vs papel, tu cuota vs aviso). Sin botón en la web que cambie la casa: el panel es
  público (solo lectura). No tenemos cuotas de Melbet/888starz: la cuota mínima del aviso es el criterio. Primeros datos: 11 puestas en
  1xBet, 1 «no estaba» (92 % disponible), tu cuota −0,3 % vs el aviso.
- **Link del partido en la casa activa (23/10):** `/dominio melbet melbet.com` (y `888starz`) guarda el dominio de cada casa; el aviso y los
  botones de la web arman el link cambiando SOLO el dominio del link de 1xBet (supone que la ruta /en/line/<deporte>/<liga>/<partido>
  es igual en la familia 1xBet; NO verificado: el usuario lo prueba con el próximo aviso). Sin dominio puesto, el aviso dice cómo ponerlo.
- **Verificado con capturas del usuario (23/10):** 1xBet (`ve1xbet.com`), Melbet (`melbet.com.ve`) y 888starz muestran las MISMAS cuotas en
  el mismo partido y Melbet usa la misma ruta y el mismo ID que 1xBet (`/live|line/<deporte>/<liga-id>-slug/<partido-id>-slug`): solo cambia el
  dominio (y el slug de los equipos). Dominios ya guardados: 1xBet=ve1xbet.com, Melbet=melbet.com.ve. Falta el de 888starz (URL no se
  veía): `/dominio 888starz <dominio>`. Los links de 1xBet del aviso también usan ve1xbet.com (no 1xbet-col.com del escáner).
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
