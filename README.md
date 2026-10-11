# surebet3: escáner de surebets y apuestas de valor (Polymarket vs. casas de apuestas)

Bot que corre 24/7 en un VPS. Compara las cuotas de **Polymarket** (mercado de predicción con order book real)
con casas tradicionales (**1xBet** y sus clones, **Cloudbet**, **SX Bet**, Stake y Rollbit) y busca dos cosas:

1. **Surebets (arbitraje).** Se reparte el dinero entre todos los resultados de un partido en casas distintas,
   de forma que gane quien gane quede ganancia. No depende de acertar.
2. **Apuestas de valor.** La casa paga una cuota más alta que la probabilidad "justa" que marca Polymarket.
   Se gana a largo plazo si la ventaja es real, pero cada apuesta puede perder.

Nada se apuesta solo con dinero real. El bot **detecta, avisa por Telegram y mide en papel** con números
lo más reales posible: order book real, comisiones reales, retraso en vivo y mínimos de cada casa.
Las apuestas se hacen **a mano** con botones de apuesta rápida. La ejecución automática existe solo
**en simulación**, para medir si valdría la pena.

---

## Índice

- [Cómo funciona (de punta a punta)](#cómo-funciona-de-punta-a-punta)
- [Qué se mide y resultados hasta ahora](#qué-se-mide-y-resultados-hasta-ahora)
- [Fuentes de datos](#fuentes-de-datos)
- [Mercados soportados](#mercados-soportados)
- [Precios reales: comisiones y order book](#precios-reales-comisiones-y-order-book)
- [Cuentas en papel (tracks)](#cuentas-en-papel-tracks)
- [Filtros de seguridad](#filtros-de-seguridad)
- [Apuestas de valor y el filtro "oro"](#apuestas-de-valor-y-el-filtro-oro)
- [Simulaciones de automatización](#simulaciones-de-automatización)
- [Alertas de Telegram y apuesta rápida](#alertas-de-telegram-y-apuesta-rápida)
- [Panel web](#panel-web)
- [Despliegue en el VPS](#despliegue-en-el-vps)
- [Uso local](#uso-local)
- [Configuración (.env)](#configuración-env)
- [Estructura del código](#estructura-del-código)
- [Límites y reglas que el proyecto respeta](#límites-y-reglas-que-el-proyecto-respeta)

---

## Cómo funciona (de punta a punta)

```
 Fuentes ─────────────► Normalización ──► Emparejamiento ──► Motor de arbitraje / valor
 Polymarket (Gamma+CLOB)  equipos, deporte,   mismo partido en      particiones de resultados
 1xBet (feed público)     mercado, hora       todas las casas       (cada resultado cubierto 1 vez)
 Cloudbet (Feed API)                          (fuzzy + alias +       + comisiones + order book
 SX Bet (API pública)                          ventana de hora)
 OddsPapi / The Odds API                                                   │
                                                                           ▼
                        Filtros de seguridad ◄── oportunidades ──► Cuentas en papel (SQLite)
                                 │                                  · real ($1000)
                                 ▼                                  · medición ($100 fijo)
                  Telegram "lista para apostar"                     · automático (simulado)
                  + botones de apuesta rápida                         │
                                 │                                    ▼
                                 └────────► Panel web 24/7 ◄──── liquidación con resultados
                                                                 oficiales (Polymarket / SX)
```

Cada ciclo (unos segundos):

1. **Descarga** partidos y cuotas. Polymarket trae los order books completos; 1xBet trae prepartido y en vivo;
   Cloudbet y SX Bet se consultan con cachés por liga para gastar poco.
2. **Empareja** el mismo partido entre casas: nombres con fuzzy matching y alias (`aliases.json`), deporte
   y hora de inicio con una diferencia máxima.
3. **Busca combinaciones** que cubran todos los resultados exactamente una vez. Ejemplos: Local / Empate /
   Visitante; Más / Menos 2.5; Gana A / Gana B. Si `Σ 1/cuota < 1` después de comisiones, es surebet.
4. **Reparte el monto** para que el cobro sea igual en todos los resultados, recorriendo el order book real
   de Polymarket al monto exacto.
5. **Aplica los filtros de seguridad**: margen sospechoso, cuota "palpable" fuera de mercado, hora distinta
   y deporte problemático.
6. **Registra en papel**, **avisa** por Telegram las que valen la pena y **liquida** sola cuando el partido termina.

---

## Qué se mide y resultados hasta ahora

Todo queda en `data/opportunities.db` y se ve en el panel. Los datos del 10/10/2026, después de unas 24 h
corriendo en el VPS:

### Surebets (track de medición, $100 fijo por operación)

| Tipo | Operaciones liquidadas | Ganancia | Neto sobre lo apostado | Pérdidas |
|---|---|---|---|---|
| **Prepartido** | 149 | **+$210.55** | **1.58 %** | 0 |
| En vivo (solo para medir) | 314 | +$495.47 | 1.77 % | 0 en papel* |

\* En vivo hay retraso de aceptación y cambios de cuota. En papel se simula un retraso de 8 s, pero en la
realidad se rechazan muchas apuestas. Por eso **la cuenta real solo toma prepartido**.

Por combinación de casas (prepartido):

| Combinación | Neto medio | Comentario |
|---|---|---|
| **1xBet + Polymarket** | ~1.3 % | Da casi toda la ganancia. Es el volumen principal |
| Polymarket + SX Bet | ~1.6 % | Muy buena, pero aparece poco |
| 1xBet + SX Bet | ~0.9 % | |
| Cloudbet + Polymarket | ~0.8 % | Pocas, pero **100 % automatizables por API** |

Volumen: unos **73 partidos distintos por día** con surebet prepartido que dura 60 s o más. Unos 47 de ellos
aguantan $300 o más. Hay más actividad entre las 07:00 y las 10:00 (hora de Venezuela).

**Cuenta "real" en papel** ($1000, $100 por surebet, solo prepartido que empieza en 6 h o menos, las mejores
primero): 23 ganadas, +$16.59 y ninguna perdida. Es más lenta porque espera a tener efectivo libre.

### Apuestas de valor (track de medición, $100 por apuesta)

| Grupo | Apuestas | Ganadas | Ganancia | ROI |
|---|---|---|---|---|
| **Prepartido "oro"** (filtro de abajo) | 113 | 56 | **+$1 884** | **+16.8 %** |
| Prepartido resto | 80 | 37 | +$710 | +8.9 % |
| En vivo | 322 | 160 | +$3 587 | +11.1 % |

Por casa (prepartido): **1xBet +19 %** (168 apuestas); SX Bet −15 % (21); la cuenta extra de 1xBet −100 % (4).

> ⚠️ Es **un solo día** de datos. Con una ventaja esperada de 2 a 7 %, un ROI de 17 % es en buena parte
> suerte (varianza). La señal más confiable es el **CLV** (si la cuota se movió a favor antes del partido).
> El CLV de prepartido es de alrededor de **+1 %**, con 66 % de apuestas positivas, y la franja de EV 2–4 % da
> una mediana de **+3.2 %**. El CLV en vivo es **negativo**, así que en vivo no se apuesta valor. Hacen falta
> semanas de datos para confirmar el ROI. Se revisa con `python clv.py`.

---

## Fuentes de datos

| Fuente | Qué trae | Costo / límite | Vivo |
|---|---|---|---|
| **Polymarket** (Gamma + CLOB) | Partidos, order books, comisiones (`feeSchedule`), resultados | Gratis | Sí |
| **1xBet**, feed público (`1xbet-col.com`, partner 285) | Todas las ligas. 888starz, Melbet, Betwinner, 22Bet y Megapari tienen **las mismas cuotas** | Gratis. **Nunca hacer ráfagas**: bloquea la IP | Sí (cada 10 s) |
| **Cloudbet** Feed API | Cuotas, `maxStake` y `marketUrl` | Gratis con API key | Sí |
| **SX Bet** API v3 | Order book del exchange y resultados oficiales | Gratis, lectura sin key | Sí (8 s de retraso) |
| OddsPapi v4 | Stake, Rollbit y Pinnacle | 250 peticiones/mes gratis | No |
| The Odds API | Unas 40 casas europeas | 500 créditos/mes gratis | Con retraso |

---

## Mercados soportados

| Mercado | Polymarket | 1xBet / Cloudbet / SX |
|---|---|---|
| 1X2 (y doble oportunidad) | 3 mercados SÍ/NO; el NO cubre los otros dos resultados | 1, X, 2, 1X, 12, X2 |
| Ganador (2 vías) | Outcomes con los dos equipos | Ganador / con prórroga |
| 1X2 del 1er tiempo | "Halftime Result" | Sub-partido "1st half" |
| Más/Menos (líneas .5) | Totals | Grupo de totales |
| Más/Menos del 1er tiempo | first_half_totals | Sub-partido "1st half" |

Las líneas enteras (2, 3) se ignoran porque pueden devolver el dinero. Las surebets triples se soportan.

---

## Precios reales: comisiones y order book

Se usa el precio real, nunca el teórico:

- **Polymarket taker**: comisión `C × rate × p × (1 − p)` con el `rate` de cada mercado (0.05 en deportes y
  0.03 en algunos). Se compra contra el **ask real**, recorriendo niveles hasta completar el monto, con el
  mínimo de shares. Las órdenes **maker** (límite) no pagan comisión.
- **SX Bet**: 1 % sobre la ganancia de la apuesta taker ganadora. Se modela como precio efectivo
  `p / (0.99 + 0.01p)`.
- **Cloudbet**: cuota tal cual, con tope en el `maxStake` que informa la API.
- En los avisos, la cuota de Polymarket aparece **exacta, con la neta de comisión entre paréntesis**:
  `@ 2.10 (con comisión: 2.06)`.

---

## Cuentas en papel (tracks)

| Track | Capital | Qué toma | Para qué |
|---|---|---|---|
| `real` | $1000, $100 por surebet | Solo **prepartido**, que empieza en **6 h o menos**, mejor neto primero, neto mínimo 0.5 % (sube a 1 % y 1.5 % cuando queda poco efectivo libre) | Simular lo que pasaría con la cuenta de verdad |
| `medicion` | $100 fijo por operación | **Todo**: prepartido, vivo, surebets y valor | Medir dónde está la ventaja |
| `auto` | $1000 aparte | Lo que el bot automático habría llenado | Medir la automatización |

Las operaciones se liquidan solas con el resultado oficial de Polymarket o, si el partido no está ahí, con el
de SX Bet. Un 50/50 se anula y devuelve lo apostado. No se entra en partidos ya empezados.

---

## Filtros de seguridad

Una oportunidad bloqueada se marca con ⛔, no se avisa y no entra a la cuenta real:

| Filtro | Por qué |
|---|---|
| Tenis con margen < 2 % | Retiros y reglas de abandono distintas entre casas |
| Cuota de la casa > 12 % sobre la probabilidad justa de Polymarket | "Palp": error de cuota que la casa anula |
| Hora de inicio distinta en más de 15 min | Posible partido mal emparejado |
| Hockey (NHL) en 1xBet | 1xBet liquida a tiempo reglamentario y Polymarket incluye prórroga |
| Básquet en 1xBet con empate bloqueado | Riesgo de falso arbitraje (`REGULATION_DRAW`) |

---

## Apuestas de valor y el filtro "oro"

Una apuesta de valor es cuando la casa paga más que la probabilidad justa (sin margen) de Polymarket:
`EV = cuota_casa × p_justa − 1`.

Solo se avisan las **"oro"**, porque es donde la medición (CLV) muestra ventaja real:

- Prepartido (en vivo el CLV es negativo)
- EV entre **2 % y 7 %** (por encima de eso suele ser un error de cuota o un emparejamiento malo)
- Cuota entre **1.6 y 4**
- Empieza en **6 h o menos**
- Polymarket con profundidad suficiente para servir de referencia

Monto sugerido: Kelly fraccional (¼), con tope del 3 % de la banca.

---

## Simulaciones de automatización

Las dos están en la pestaña **"Automático (simulado)"** del panel, junto al **embudo** de todas las surebets automatizables de 7 días (qué pasó con cada una: en vivo, duró poco, intentada, entró). **No envían ninguna orden real.**

1. **Ejecución automática Cloudbet / SX + Polymarket** (`surebet/execsim.py`): arma el payload exacto que
   iría a cada API (Cloudbet `bets/place`, orden de Polymarket, orden de SX), espera 5 s de latencia y
   comprueba si la cuota todavía está. Si está, la operación entra en la cuenta `auto`.
2. **1xBet + Polymarket con orden límite** (`surebet/makersim.py`): se apuesta en 1xBet y en Polymarket se deja
   una orden límite **1¢ por encima del mejor comprador**. Esa orden no paga comisión y nunca pasa del precio
   que asegura ganancia. Si nadie la toma en 15 min (o a 10 min del inicio), se compra al precio publicado.
   Cada intento se compara con haber comprado al instante.

Para pasar a dinero real, la simulación tiene que mostrar **más de 70 % de órdenes llenadas** con neto positivo
durante semanas. Después se empezaría con montos chicos, un interruptor de apagado y cuentas en países permitidos.

---

## Botón de Telegram: "Ya aposté en 1xBet → comprar en Polymarket"

Cada aviso 1xBet + Polymarket trae un botón (`surebet/polybuy.py`). El flujo es así:

1. Apuestas en 1xBet a mano.
2. Tocas el botón.
3. El bot lee el libro de Polymarket **en ese momento** y compra las shares exactas que igualan el cobro de 1xBet.
   Solo compra si el costo total, con comisión, todavía deja ganancia. Si el precio subió, **no compra** y avisa.

Las protecciones son estas:

- **Modo DEMO por defecto** (`POLY_LIVE=0`): anota lo que habría comprado y no envía nada.
- **Solo obedece a tu chat** (`TELEGRAM_CHAT_ID`).
- **Orden límite FOK:** se llena entera al momento, con precio tope, o se cancela.
- **Topes:** `POLY_MAX_ORDER_USD` por orden y `POLY_MAX_DAILY_USD` por día.
- **Uso:** cada botón vale 10 min y se usa una sola vez.
- **Comandos:** `/estado`, `/pausa` (apaga todo) y `/activar`.

Para pasar a real:

1. Instala la librería en el VPS: `pip install py-clob-client`.
2. Pon `POLY_PRIVATE_KEY` (y `POLY_FUNDER` / `POLY_SIGNATURE_TYPE` si entras con email) **solo en el `.env` del VPS**.
3. Pon `POLY_LIVE=1` y reinicia el servicio.

Todo queda en la tabla del panel (pestaña Principal).

## 🛟 Valor con red de seguridad (la idea más prometedora)

Las apuestas de valor "oro" tienen **CLV positivo**: después de que apuestas, el precio de Polymarket tiende a moverse
a tu favor. Eso significa que el **lado contrario se abarata**. Si dejas una **orden límite esperando** en Polymarket
(maker, sin comisión) al precio que asegura +1 %, el mercado suele venir a llenarla. En ese momento la apuesta de valor
**se convierte en surebet**.

Estudio con 81 apuestas oro reales, con el historial de Polymarket minuto a minuto hasta 10 min antes del partido:

| Cómo se cubre | Se logra asegurar ≥ 1 % |
|---|---|
| Comprando al precio publicado (taker: comisión + spread) | ~25 % de las apuestas |
| **Orden límite esperando (maker, sin comisión)** | **~60 %** (con +1,5 %: ~47 %) |

Las que no se llenan se cancelan antes del partido y quedan como apuesta de valor, que también tiene ventaja positiva.
Resultado: unas **60 surebets extra por día** sin apuro humano. La apuesta en 1xBet dura minutos, y la orden de
Polymarket espera sola.

- **En el aviso de Telegram:** cada apuesta de valor trae la línea "🛟 Red de seguridad" con shares, lado y precio límite.
- **Simulación en vivo:** pestaña Valor, sección "Valor con red de seguridad" (`surebet/makersim.py`, modo `value_net`).
  Compara con la misma apuesta sin red.
- **Ojo:** el modelo de llenado es optimista (la cola de órdenes en Polymarket existe). Hay que confirmarlo con la
  simulación en vivo durante varios días.

## Pestaña "Valor" del panel

- **Cuenta en papel propia** de apuestas de valor "oro" ($1000, Kelly/4 con tope de 3 %): curva y cada apuesta con
  cuota tomada, cuota justa de Polymarket, EV, monto, **CLV**, resultado y ganancia.
- **Tablas por franja de EV, cuota, casa y deporte**, con ROI, CLV medio y porcentaje de CLV positivo.
- **Medición de todas las señales de valor** ($100 fijos), filtrable entre "solo oro" y "todas", para ver qué filtro funciona.

## Alertas de Telegram y apuesta rápida

- **"Lista para apostar"**: casa, cuota exacta (y neta), monto de cada lado, cuota mínima aceptable y
  precio máximo en Polymarket.
- **Botones** (teclado inline): abren cada casa en el partido. En el panel, el botón copia el monto y abre el link.
- **Rotación de casas** (`ROTATE_BOOKS`): reparte las apuestas entre clones de 1xBet. Es **una cuenta por casa,
  a tu nombre**, para que ninguna sola se limite rápido.
- **Resumen diario** a las 09:00 (hora de Caracas).

---

## Panel web

- Público y de solo lectura en el VPS: `http://<IP_VPS>/surebet/` (nginx hace de proxy al servicio, en `127.0.0.1:8790`).
- Pestaña **Principal (manual)**: capital, curva, señales del momento, operaciones y la tabla
  "¿Dónde está la ventaja?" por tipo, casa y combinación.
- Pestaña **Automático (simulado)**: cuenta `auto`, ejecuciones simuladas y la prueba de orden límite.

---

## Despliegue en el VPS

- Servicio systemd `surebet3` en `/opt/apps/surebet3` (ver `deploy/surebet3.service`), corriendo `python scan.py --headless`.
- Consume poco porque comparte el VPS con otros bots: `Nice=10`, `MemoryMax=550M`, `CPUQuota=50%` y
  `MALLOC_ARENA_MAX=2`. Usa unos 420 MB de RAM y 20 % de CPU.
- SQLite en modo WAL con `timeout=30`. La liquidación primero descarga los resultados y después escribe, en lotes.
- `subir_vps.bat` sube el código y reinicia el servicio. `panel_vps.bat` abre el panel.

```bash
systemctl status surebet3
journalctl -u surebet3 -f
```

---

## Uso local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python scan.py --once --quiet
python scan.py --headless
python scan.py --no-sxbet
python dashboard.py
python clv.py
python -m pytest -q
```

Qué hace cada comando:

| Comando | Para qué |
|---|---|
| `python -m venv .venv` + `activate` + `pip install -r requirements.txt` | Crear el entorno e instalar dependencias |
| `copy .env.example .env` | Crear tu configuración (después rellenas tus keys) |
| `python scan.py --once --quiet` | Un ciclo, sin ruido |
| `python scan.py --headless` | Modo servicio, con una línea de estado cada 5 min |
| `python scan.py --no-sxbet` | Escanear sin SX Bet |
| `python dashboard.py` | Solo el panel |
| `python clv.py` | ¿La ventaja de las apuestas de valor es real? |
| `python -m pytest -q` | Tests (82) |

---

## Configuración (.env)

Copia `.env.example` a `.env`. **El `.env` nunca se sube** (está en `.gitignore`). Ahí van las keys: Telegram,
Cloudbet, OddsPapi y The Odds API. Las principales:

| Variable | Qué hace |
|---|---|
| `MY_BOOKS` | Casas donde tienes cuenta (solo se toman esas) |
| `ROTATE_BOOKS` | Clones de 1xBet para rotar en los avisos |
| `CLOUDBET_API_KEY` | Key de Cloudbet |
| `SXBET_ENABLED` | Activa o desactiva SX Bet |
| `PAPER_REAL_MAX_HOURS`, `PAPER_REAL_MIN_NET_PCT` | Reglas de la cuenta real |
| `PAPER_INCLUDE_LIVE` | 0: la cuenta real no toma vivo |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Alertas |
| `PANEL_URL`, `DAILY_REPORT_HOUR` | Link del panel en los avisos y hora del resumen diario |

---

## Estructura del código

```
scan.py                     bucle principal: fuentes → emparejamiento → motor → papel → alertas
clv.py                      análisis de CLV de las apuestas de valor
dashboard.py                panel sin escanear
surebet/
  arb.py                    motor de particiones, reparto de montos, valor
  pricing.py                comisiones, recorrido del order book
  matching.py               emparejamiento de partidos (fuzzy + alias + caché)
  safety.py                 filtros de seguridad y filtro "oro"
  paper.py                  cuentas en papel (real / medición / auto) y liquidación
  execsim.py                simulación de ejecución automática por API
  makersim.py               simulación 1xBet + Polymarket con orden límite
  alerts.py                 Telegram, botones, rotación de casas, formato "lista para apostar"
  dashboard_server.py       API del panel (/api/state)
  status.py, store.py       estado y persistencia
  sources/                  polymarket, onexbet, cloudbet, sxbet, oddspapi, oddsapi
static/index.html           panel web
deploy/surebet3.service     unidad systemd
reports/, research_notes/   investigación: casas automatizables, APIs, riesgos
tests/                      82 tests (pytest)
```

---

## Límites y reglas que el proyecto respeta

- **No** evade geobloqueos, Cloudflare, antibots ni captchas, y **no** se disfraza de humano.
- **No** hay bots de navegador para casas sin API (1xBet se apuesta a mano).
- **Una sola cuenta por casa, a nombre propio.** Rotar entre casas distintas sí; varias cuentas en la misma
  casa o cuentas de terceros, no.
- Las órdenes reales en Polymarket solo se harían desde un país permitido y con cuenta legítima. Hoy todo está en simulación.
- Las keys y tokens van solo en `.env`.
- Al feed de 1xBet se le pide con intervalos prudentes, sin ráfagas.

> Las surebets reducen el riesgo de resultado, pero no eliminan el riesgo operativo: apuestas anuladas,
> límites de cuenta, cambios de cuota y diferencias de reglas entre casas. Este proyecto mide y avisa;
> las decisiones y el dinero son responsabilidad de quien apuesta.
