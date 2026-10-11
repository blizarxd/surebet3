# Exchanges tradicionales, casas sharp y mercados de predicción regulados con API (candidatos para emparejar con Polymarket)

Notas de investigación al 10-oct-2026. Contexto: escáner Polymarket vs 1xBet / SX Bet; usuario reside en Venezuela / Colombia (Cúcuta); bots en VPS Hetzner (Finlandia). Varias webs primarias (developer.betfair.com, docs.smarkets.com, betfair.com T&C, help.smarkets.com, kalshi.com fee PDF) devolvieron 403/429 al consultarlas; donde fue así se indica que el dato viene de fuente secundaria. No se incluyen instrucciones para evadir geobloqueos, KYC o regulación.

## 1. ¿API oficial para colocar apuestas/órdenes y qué requisitos tiene?

### Takeaway
Solo tres venues tienen hoy una API de trading oficial, documentada y de autoservicio para minoristas: Betfair Exchange (clave "live" con tasa de activación de £499, cuenta verificada por KYC y con fondos, uso personal), Matchbook (las credenciales de login dan acceso inmediato) y Kalshi (REST/WebSocket/FIX, con niveles de límite por volumen). Smarkets tiene API de streaming/REST con SDK, pero no se pudo confirmar su proceso de acceso actual. Pinnacle cerró su API al público el 23-jul-2025. ProphetX, Novig, Sporttrade y BetOpenly no tienen API pública; como mucho ofrecen integraciones solo para partners o market makers.

### Cited Findings
**Betfair Exchange (API-NG / Stream API)**
- Hay dos claves por cuenta. La "Delayed" sirve para desarrollo y test y opera sobre el exchange real de producción, no sobre un sandbox. La "Live" sirve para apuestas en vivo y se crea inactiva hasta que se activa. Solo se permite un set de claves por cuenta. — [Betfair Developer Docs, página "Application Keys" vía API REST de Confluence](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=title~%22Application%20Keys%22&expand=body.view)
- La clave Live cuesta £499 de activación, que se debita de la cuenta Betfair al activarla. Antes hay que completar las pruebas con la clave Delayed, tener la cuenta "fully verified in line with our KYC policy" y con fondos para cubrir los £499. Las cuentas de Betfair Australia usan otra URL de solicitud. — [Betfair Developer Docs (Application Keys)](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=title~%22Application%20Keys%22&expand=body.view)
- **Conflicto:** otros snippets de las mismas docs indexados por el buscador citan una tasa única de **£299**. Es probable que sea una versión antigua de la página. La versión actual que se recuperó dice £499. — [Betfair Developer Docs (snippet indexado)](https://betfair-developer-docs.atlassian.net/wiki/x/URAp)
- "App Key generation is for personal betting purposes only"; "All data/API usage in any commercial context must be approved by Betfair"; "Unauthorised commercial usage will be identified & blocked." — [Betfair Developer Docs (Application Keys)](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=title~%22Application%20Keys%22&expand=body.view)
- Según un snippet de las docs de Betfair (no se pudo releer la página completa), no se aceptan solicitudes de licencia desde India, Bangladesh, Sri Lanka ni EAU. — [Betfair Developer Docs (snippet indexado)](https://betfair-developer-docs.atlassian.net/wiki/x/URAp)
- Betfair también tiene una "Vendor API" con licencia para afiliados y terceros comerciales, además de las APIs de Betting, Accounts e Historic Data. — [API Evangelist – Betfair](https://providers.apievangelist.com/providers/betfair/)

**Matchbook**
- "Registered customers can use the API immediately with standard login credentials." Para más volumen hay que pasar a un "API Plan" escribiendo a api@matchbook.com. Se cobra un "Trader Tier Fee" o un "Commercial Tier Fee", pero la página no da los importes. — [Matchbook Developers – Pricing](https://developers.matchbook.com/docs/pricing)

**Smarkets**
- Existe un SDK oficial en Python (smk_python_sdk, docs v9.4): un cliente de streaming que conecta al host de streaming en el puerto 3701 y coloca órdenes (OrderCreate, GOOD_TIL_CANCELLED). Soporte en support@smarkets.com. — [Smarkets Python API Client (readthedocs)](https://smarkets-python-sdk.readthedocs.io/)
- En 2018 Smarkets dio acceso a su API y un esqueleto de bot de trading en un taller de EuroPython. — [EuroPython 2018: Introducing Smarkets](https://www.python.org/pipermail/python-list/2018-July/886455.html)
- Según una "skill" no oficial de la comunidad, para operar se requiere un session token y existe además un API token opcional solo para datos de mercado. Es indicativo, no oficial. — [tessl.io – CloddsBot smarkets skill](https://tessl.io/registry/skills/github/alsk1992/CloddsBot/smarkets/review)

**Betdaq**
- En 2018 Betdaq lanzó una "Commercial API" orientada a partners sportsbook. — [SBC News (2018)](https://sbcnews.co.uk/europe/2018/09/18/betdaq-launches-next-level-commercial-api/)
- Un wrapper comunitario en Python usa usuario y contraseña (`APIClient('username','password')`) y apunta a docs en api.betdaq.com/v2.0. No se verificó que sigan vigentes. — [PyPI betdaq](https://pypi.org/project/betdaq)
- RapidAPI dice que "The BETDAQ API is now deprecated". Es un tercero, pero es una señal de alarma. — [RapidAPI directory – Betdaq](https://rapidapi.com/blog/directory/betdaq)

**Pinnacle**
- "Access to Pinnacle API suite has been closed for the general public since July 23rd, 2025." Pinnacle ofrece "bespoke data services for select high value bettors & commercial partnerships" (también apoya proyectos académicos y de handicapping prepartido). Para solicitarlo hay que escribir a api@pinnacle.com con el caso de uso. Los endpoints siguen listados como "Current" y exigen cuenta con fondos. — [Pinnacle API documentation (GitHub oficial)](https://github.com/pinnacleapi/pinnacleapi-documentation)
- Tras el cierre aparecieron revendedores de datos de Pinnacle (PinnWire, PinnOdds, pinnapi). Son fuentes comerciales con interés propio. — [sportsapis.dev – PinnWire](https://sportsapis.dev/apis/pinnwire); [sportsapis.dev – pinnapi](https://sportsapis.dev/apis/pinnapi)

**Kalshi**
- Ofrece acceso programático REST y FIX, que comparten los mismos presupuestos de límite. Niveles: Basic (al completar el registro), Advanced (se pide con el endpoint "Upgrade Account API Usage Level"), y Expert/Premier/Paragon/Prime/Prestige, que se ganan por cuota de volumen (0,075 %, 0,125 %, 0,25 %, 0,50 % y 1,00 % respectivamente). — [Kalshi Docs – Rate limits](https://docs.kalshi.com/getting_started/rate_limits)
- El WebSocket de actualizaciones en vivo requiere autenticación. — [Kalshi Docs – Quick start market data](https://docs.kalshi.com/getting_started/quick_start_market_data)

**ProphetX / Novig / Sporttrade / BetOpenly / PredictIt**
- ProphetX: "does not offer a public developer API for odds data… no developer portal, no API key system". La integración es solo para partners vía su equipo de afiliados, con límites negociados. Fuente: vendedor de datos, con interés propio; el artículo es probablemente anterior a su paso a DCM. — [OddsPapi – ProphetX API](https://oddspapi.io/blog/?p=2839)
- ProphetX obtuvo de la CFTC el registro como DCM y DCO (anunciado el 11-jun-2026) y lanzó en todo EE. UU. el 18-jun-2026. Solo opera deportes. — [Covers – aprobación](https://www.covers.com/industry/prophetx-prediction-market-license-approved-cftc-sports-betting-june-2026); [Covers – lanzamiento](https://www.covers.com/industry/prophetx-launches-prediction-markets-in-us-one-week-after-cftc-approval-june-18-2026)
- Novig: una tabla comparativa de 2026 lo describe como regulado por la CFTC, P2P, con fondeo en USD fiat y "no public API". — [Coinpedia – 10 prediction market platforms 2026](https://coinpedia.org/top-10/10-prediction-market-platforms-worth-knowing-in-2026/)
- Una oferta de empleo de Novig (Developer Relations Engineer) menciona "market makers and liquidity providers, trading firms, and B2B or embedded partners integrating our APIs". Sugiere que existe una API, pero para partners. — [Wellfound – Novig DevRel job](https://wellfound.com/jobs/4527387-developer-relations-engineer)
- Sporttrade solicitó el estatus de DCM/DCO a la CFTC (anunciado el 4-feb-2026) y cerró su exchange con licencias estatales (NJ hasta el 25-may, AZ/CO/IA/VA hasta el 25-jun-2026). A 18-may-2026 la aprobación seguía pendiente. — [Covers – Sporttrade CFTC](https://www.covers.com/industry/sporttrade-seeks-cftc-approval-to-take-trading-platform-nationwide-feb-4-2026); [Gambling.com – Sporttrade exits](https://www.gambling.com/us/news/sporttrade-exits-us-sports-betting-market-in-pivot-to-prediction-markets)
- BetOpenly es un exchange P2P con comisión del 1 % y presencia en 45 estados de EE. UU. (podcast de 2025). No se halló API. — [Betting Startups Podcast ep. 167](https://podcastrex.com/shows/the-betting-startups-podcast/ep-167-reduced-juice-for-all-w-gino-donati-from-betopenly)
- PredictIt tiene una API pública de solo lectura sin autenticación (`https://www.predictit.org/api/marketdata/all`). Las categorías son solo políticas y la licencia de los datos es "for non-commercial use". — [rpredictit (CRAN)](https://search.r-project.org/CRAN/refmans/rpredictit/html/00Index.html)

### Inferences
- Matriz rápida (inferida de los hallazgos de esta nota; ver la sección 4 para elegibilidad):
  - **Betfair Exchange**: API de trading oficial (£499), lectura con delay gratis, máxima liquidez. Acceso desde VE/CO no confirmado.
  - **Matchbook**: API inmediata para clientes, lecturas cobradas a £100 por millón de GET al mes. Elegibilidad LatAm desconocida.
  - **Smarkets**: hay API y SDK; proceso de acceso y elegibilidad LatAm sin confirmar.
  - **Betdaq**: API posiblemente obsoleta o solo comercial; mercado UK/IE.
  - **Kalshi**: API oficial completa, lectura pública gratis. Colombia probablemente elegible y Venezuela restringida (ver sección 4).
  - **Pinnacle**: API cerrada a nuevos usuarios desde jul-2025; además Coljuegos la bloquea en Colombia.
  - **ProphetX / Novig / Sporttrade / BetOpenly**: solo EE. UU. (inferencia por su modelo DCM/estatal) y sin API pública. No sirven para este usuario.
  - **PredictIt**: sin deportes. No sirve.
- Para un bot que opere desde Colombia, Kalshi es el único venue "exchange regulado con API oficial" con indicios documentales de aceptar residentes colombianos. Betfair sería técnicamente el mejor, pero falta confirmar que acepte cuentas Exchange de VE/CO.
- Como la clave Live de Betfair no permite uso de solo lectura ("read-only access using the Live App Key isn't permitted"), un uso puramente de escaneo debería hacerse con la Delayed key o con el Stream API, siempre que se apueste realmente.

### Gaps
- No se pudo acceder a docs.smarkets.com ni a la ayuda de Smarkets (403). Queda sin confirmar si el trading por API requiere aprobación, tarifa o un mínimo de volumen.
- No se confirmó si Betdaq mantiene en 2026 una API para minoristas.
- No se encontró documentación oficial de API (ni privada para minoristas) de Novig, ProphetX, Sporttrade ni BetOpenly posterior a sus cambios regulatorios de 2026.
- Orbit Exchange y los white labels asiáticos no tienen API oficial documentada para usuarios finales (ver sección 5).

## 2. ¿Lectura gratis o barata de cuotas/libros en casi tiempo real? Límites de peticiones

### Takeaway
Kalshi ofrece la mejor lectura gratuita: los endpoints REST de series, eventos, mercados y orderbooks son públicos y no piden clave; el WebSocket sí exige autenticación. La Delayed key de Betfair es gratis, pero tiene un retraso variable de 1 a 180 s y solo 3 niveles de precio. Matchbook cobra la lectura a £100 por cada millón de GET al mes. Pinnacle ya no da lectura pública; solo quedan revendedores. PredictIt es gratis, pero no tiene deportes.

### Cited Findings
- **Kalshi**: "Kalshi provides several public endpoints that don't require API keys". Base `https://external-api.kalshi.com/trade-api/v2` (series, events, markets, orderbooks). El WebSocket requiere autenticación. — [Kalshi Docs – Quick start market data](https://docs.kalshi.com/getting_started/quick_start_market_data)
- **Kalshi, presupuestos en tokens por segundo (lectura/escritura)**: Basic 200/100; Advanced 300/300; Expert 600/600; Premier 1.200/1.200; Paragon 2.400/2.400; Prime 4.800/4.800; Prestige 12.000/9.600. La mayoría de peticiones cuesta 10 tokens: Basic equivale a unas 20 lecturas/s y 10 escrituras/s sostenidas, y Premier a unas 120 órdenes/s. La cuota de volumen se calcula como volumen propio de 30 días / (2 × volumen total del exchange del mes previo). Los niveles duran 30 días y se renuevan a diario. — [Kalshi Docs – Rate limits](https://docs.kalshi.com/getting_started/rate_limits)
- **Betfair Delayed key**: "The delay is variable between 1-180 second snapshots". No devuelve `totalMatched` ni EX_ALL_OFFERS en listMarketBook, da solo 3 niveles de precio y no incluye "Total Matched by Selection" ni los precios BSP Far/Near. El Stream API con clave Delayed está "Limited to 2 connections & 200 markets". — [Betfair Developer Docs (Application Keys)](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=title~%22Application%20Keys%22&expand=body.view)
- **Betfair, límites de datos**: "sum(Weight) * number market ids must not exceed 200 points per requests". Si se supera, devuelve TOO_MUCH_DATA (p. ej., listMarketBook con EX_ALL_OFFERS pesa 17 y EX_ALL_OFFERS + EX_TRADED pesa 32). Tras fallos de login hay bloqueo de 20 min, y las conexiones keep-alive ociosas se cierran cada 3 min. Recomienda "Use the Stream API instead of polling wherever possible". — [Betfair Developer Docs (límites/transacciones)](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=text~%22transaction%20charge%22%20OR%20title~%22Data%20Request%20Limits%22%20OR%20title~%22Market%20Data%20Request%20Limits%22&expand=body.view&limit=5)
- **Matchbook**: "Each set of 1 million GET requests in a calendar month will be charged at 100 GBP". Las escrituras no se cobran "assuming that they are made at reasonable frequency". Se factura a fin de mes con débito automático. — [Matchbook Developers – Pricing](https://developers.matchbook.com/docs/pricing)
- **Matchbook, límites por minuto y cuenta**: Account 300, Events 700, Security 200, Navigation 100, Reports 40, Betting Write 3.000. Excederlos provoca un bloqueo de 10 min por defecto. Prohíbe usar la API "for benchmarking or competitive purposes" y redistribuir el contenido sin consentimiento. — [Matchbook Developers – Fair Usage Policy](https://developers.matchbook.com/docs/fair-usage-policy)
- **Pinnacle**: API cerrada al público (ver sección 1). Algunos agregadores (odds-api.io) dicen haber sustituido a Pinnacle por Betfair Exchange, SingBet y Polymarket como referencias "sharp". — [odds-api.io blog (vía sportsapis.dev)](https://odds-api.io/blog)
- **PredictIt**: lectura pública sin autorización, solo mercados políticos y licencia no comercial. — [rpredictit (CRAN)](https://search.r-project.org/CRAN/refmans/rpredictit/html/00Index.html)
- **ProphetX**: según un vendedor de datos, raspar sus endpoints internos es frágil y va contra sus términos de servicio. — [OddsPapi – ProphetX API](https://oddspapi.io/blog/?p=2839)

### Inferences
- Para un escáner de surebets en vivo, la Delayed key de Betfair (hasta 180 s de retraso, 3 niveles) no basta para disparar apuestas. Sí sirve para detectar candidatos y luego confirmar con la clave Live.
- Matchbook es barato a escala modesta: un sondeo cada 2 s de un endpoint durante un mes son unos 1,3 M de GET, alrededor de £130 al mes. Es un cálculo propio con la tarifa citada.
- Kalshi permite escanear gratis por REST sin cuenta. El WebSocket, más eficiente, exige cuenta autenticada, lo que a su vez exige elegibilidad por país.

### Gaps
- No se obtuvieron los límites de peticiones de Smarkets, Betdaq ni ProphetX/Novig.
- No se verificó si el WebSocket o el Stream de Betfair con clave Live tienen límite de mercados suscritos (solo se halló el límite de la Delayed).

## 3. Comisiones / tasas y liquidez por deporte

### Takeaway
Las estructuras de comisión difieren mucho. Betfair cobra sobre la ganancia neta por mercado (Market Base Rate menos descuentos) y además un "Expert Fee" del 20 % o 40 % a los ganadores de más de £25k en 52 semanas, que sustituyó al Premium Charge en 2025. Matchbook y Smarkets rondan el 1,5–2 % (con un 4 % para no residentes UK/IE en Matchbook según una fuente antigua). Kalshi cobra al taker 7 % × P × (1−P) y al maker 1,75 %, y Polymarket cobra en deportes un 5 % × P × (1−P) solo al taker desde el 1-jul-2026. Sumadas, estas comisiones se comen la mayoría de los gaps pequeños entre venues.

### Cited Findings
- **Betfair Expert Fee**: sustituyó desde el 6-ene-2025 al Premium Charge, que podía llegar al 60 % de las ganancias. Se basa en el beneficio bruto de 52 semanas móviles: menos de £25.000, 0 %; de £25.000 a £100.000, 20 %; más de £100.000, 40 %. Betfair dijo que el 80 % de los usuarios pagaría menos y la mitad no pagaría nada adicional. — [Racing Post](https://racingpost.com/news/britain/betfair-exchange-to-introduce-new-commission-system-for-2025-as-premium-charge-is-dropped-a7wbg0v4GCAJ)
- **Betfair Market Base Rate**: comisión sobre las ganancias netas por mercado ("Market Base Rate minus discounts"), con base variable por tipo de mercado y descuentos por volumen; no hay comisión sobre pérdidas. Fuente secundaria: no se confirmó la cifra estándar (habitualmente citada como 5 %). — [pm.wiki – Betfair vs Smarkets](https://pm.wiki/es/compare/betfair-exchange-vs-smarkets)
- **Betfair**: las dos claves llevan cargos por transacción ("Transaction Charge Applies: Yes"), pero no se recuperó el umbral de apuestas por hora ni el importe. — [Betfair Developer Docs (Application Keys)](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=title~%22Application%20Keys%22&expand=body.view)
- **Matchbook, comisión (fuentes en conflicto)**: 2 % según [Gambling.com (2026)](https://www.gambling.com/uk/betting-sites/matchbook); 1,5 % sobre ganancias netas según [OddsPapi (abr-2026)](https://oddspapi.io/blog/?p=2848); 2 % para UK, Isla de Man, Islas del Canal e Irlanda y 4 % para el resto de clientes según un foro que cita los T&C (sin fecha, posiblemente desactualizado) — [NinjaBet forum](https://ninjabetgermany.nodebb.com/post/451)
- **Smarkets**: 2 % de comisión (dato de 2013, a verificar). — [UKTech News (2013)](https://www.uktech.news/2013/07/25/betting-exchange-smarkets-rebrands-switches-focus/)
- **Betdaq**: comisión estándar del 2 %. Las promos de 0 % excluyen a quien use "API, RDT, or other trading software" (página de promo de jun-2026, solo UK e Irlanda). — [Football Ground Guide – Betdaq](https://footballgroundguide.com/betting/betdaq)
- **Kalshi**: taker `0,07 × C × P × (1−P)` (redondeado al céntimo superior); maker con tasa del 1,75 %. Por contrato a 50¢: 1,75¢ taker y 0,44¢ maker; a 10¢ o 90¢: 0,63¢ y 0,16¢. Según esta fuente el 7 % aplica "on every category". Los deportes son ~87 % de los 39.700 M USD negociados en el año a feb-2026. — [River Markets – Kalshi fees (actualizado 9-ago-2026)](https://www.rivermarkets.com/insights/kalshi-fees.html)
- **Conflicto sobre Kalshi**: otras fuentes dicen que algunas categorías, como deportes, tienen tasa menor o que hay órdenes de reposo sin comisión en la mayoría de mercados deportivos. — [Stokastic](https://www.stokastic.com/articles/prediction-markets/kalshi-fees-vs-dfs-rake); el PDF oficial de tarifas (kalshi.com/docs/kalshi-fee-schedule.pdf) devolvió 429.
- **Polymarket (para comparar)**: las comisiones en deportes se lanzaron el 18-feb-2026 ([Pine Analytics](https://pineanalytics.substack.com/p/polymarket-fee-rollout)). La "Fee Structure V2" (desde el 30-mar-2026) fijó deportes en el 3 % y desde el 1-jul-2026 en el 5 % (máx. $1,25 por 100 acciones a 50¢). Los makers no pagan y el rebate a makers en deportes bajó del 25 % al 15 %. — [River Markets – Polymarket fees](https://www.rivermarkets.com/insights/polymarket-fees.html); [startpolymarket](https://startpolymarket.com/learn/polymarket-fees/) (secundarias, no verificadas contra docs.polymarket.com)
- **ProphetX**: 2 % sobre las ganancias netas según una reseña de afiliado. No tiene brazo de trading propio; depende de market makers institucionales externos. — [Gambling.com – ProphetX review](https://www.gambling.com/us/prediction-markets/reviews/prophetx)
- **Novig**: se describe como "commission-free". — [CB Insights – Novig](https://www.cbinsights.com/company/novig)
- **BetOpenly**: comisión del 1 %. — [Betting Startups Podcast](https://podcastrex.com/shows/the-betting-startups-podcast/ep-167-reduced-juice-for-all-w-gino-donati-from-betopenly)
- **Liquidez**: en el partido Argentina vs Egipto se negociaron ~20,8 M USD en Polymarket, con 13,8 M USD de open interest en Kalshi. — [DEV Community](https://dev.to/michaelmustopo/built-a-prediction-market-arbitrage-no-sizable-arbitrage-found-36cf)
- Según guías de prácticos, en Polymarket/Kalshi una posición de unos 10.000 USD de nocional suele mover el precio 1–3 centavos. — [newyorkcityservers – arbitrage guide](https://newyorkcityservers.com/blog/prediction-market-arbitrage-guide) / [pm.wiki](https://pm.wiki/uk/learn/polymarket-kalshi-arbitrage) (no se verificó cuál de las dos lo dice)

### Inferences
- En un par Kalshi–Polymarket con las dos patas taker a precios cercanos a 50¢, el coste combinado ronda 1,75¢ + 1,25¢ = 3¢ por par de contratos (cálculo propio con las tarifas citadas). Un gap bruto menor que eso no es surebet. Con una o ambas patas como maker baja mucho.
- En Betfair, un bot de surebets rentable superaría con el tiempo el umbral de £25k/52 semanas y pagaría un Expert Fee del 20–40 %, que no aplica a Polymarket ni a Kalshi.

### Gaps
- No se confirmó el Market Base Rate actual de Betfair por deporte ni los importes de sus cargos por transacción.
- No hay datos fiables de liquidez por deporte en Smarkets, Matchbook ni Betdaq (2026).
- El calendario oficial de comisiones de Kalshi para deportes (2026) no se pudo leer directamente.

## 4. ¿Quién puede usarlos? Residentes de Venezuela y Colombia; régimen de Coljuegos

### Takeaway
En Kalshi, Venezuela está explícitamente restringida (lista de sanciones del Member Agreement) y Colombia no figura en las listas restringidas consultadas; una fuente secundaria de abr-2026 la cuenta entre los países donde opera. Para Betfair, Pinnacle, Smarkets, Matchbook y Betdaq no se encontró una lista oficial y legible que diga si aceptan residentes de VE o CO: es una incertidumbre importante. Los venues estadounidenses (ProphetX, Novig, Sporttrade, BetOpenly) están orientados a EE. UU. En Colombia solo 15 operadores tienen autorización de Coljuegos, que ha emitido 46.228 órdenes de bloqueo desde 2022 y ha bloqueado dominios de 1xBet y Pinnacle.

### Cited Findings
**Kalshi**
- "Yes, you can trade on Kalshi from many countries". La elegibilidad la rige el Member Agreement y el registro exige verificación de identidad y confirmar el país de residencia. Fondeo internacional: débito Visa/Mastercard, transferencia (mín. 1.000 USD) y cripto; retiros por débito o cripto; sin ACH, PayPal ni Venmo. Página fechada el 20-mar-2026. — [Kalshi Help Center](https://help.kalshi.com/en/articles/14026044-can-i-trade-on-kalshi-from-outside-the-united-states)
- La lista restringida (Member Agreement v1.6, sección VI, 54 jurisdicciones) incluye **Venezuela** (bloque de sanciones/conflicto), **Bolivia** y **Haití** en LatAm. **Colombia no aparece**. También cubre cualquier territorio bajo sanciones integrales de EE. UU. y puede modificarse unilateralmente. — [CoinPerps – Kalshi restricted countries](https://www.coinperps.com/learn/kalshi-restricted-countries-prediction-market-perps) (secundaria)
- Otra fuente dice que la versión del 21-sep-2026 del Member Agreement nombra 55 jurisdicciones restringidas e indica que India se añadió en jun-2026. — [Laika Labs](https://laikalabs.ai/prediction-markets/kalshi-legal-supported-restricted-countries) (secundaria, vía snippet de búsqueda)
- En oct-2025 Kalshi dijo estar disponible en "140+ countries", con un Member Agreement de 38 jurisdicciones restringidas (incluida Venezuela). — [Cointelegraph](https://cointelegraph.com/news/kalshi-300-million-raise-prediction-markets-140-countries/) (los recuentos de 38/54/55 varían según la versión)
- Sportico (oct-2025): usuarios de India, Brasil y Nigeria reportaron que no podían registrarse. Un portavoz confirmó que la app no está disponible fuera de EE. UU. (el acceso internacional es solo web). Kalshi sostuvo que "International signups are fully operational". — [Sportico](https://www.sportico.com/business/sports-betting/2025/kalshi-international-countries-access-1234874388/) (vía snippet de búsqueda; la fetch redirigió a un paywall)
- InGame (27-abr-2026) incluye a **Colombia** entre los países clasificados al Mundial 2026 que "allow Kalshi to operate" y dice que Kalshi está en 143 países. El autor lo verificó por su cuenta, sin fuente oficial. — [InGame](https://www.ingame.com/?p=48634)
- Primer impulso internacional formal: alianza con XP (Brasil) en mar-2026. — [Bloomberg Government](https://news.bgov.com/crypto/kalshi-teams-up-with-brazils-xp-for-first-international-push)
- En EE. UU., los contratos deportivos de Kalshi están en litigio con varios estados (NV, NJ, TN, MA) y los tribunales de Massachusetts los prohibieron. — [CoinPerps](https://www.coinperps.com/learn/kalshi-restricted-countries-prediction-market-perps). El 12-mar-2026 la CFTC emitió la Staff Letter 26-08 con guía para DCMs sobre contratos de eventos deportivos y un ANPRM. — [Mondaq](https://mondaq.com/unitedstates/financial-services/1760818/cftc-issues-parallel-actions-on-prediction-markets-key-takeaways-for-designated-contract-markets)

**Betfair**
- Coljuegos otorgó a Betfair en 2020 una licencia de 5 años (apuestas deportivas y no deportivas, casino). Lanzó en feb-2021 en betfair.com.co con BtoBet como proveedor de sportsbook. — [iGaming Business](https://igamingbusiness.com/betfair-receives-five-year-colombian-igaming-licence/); [CasinoBeats](https://casinobeats.com/2021/02/05/flutters-betfair-gains-licence-debuts-in-colombia/)
- Más tarde Stake.com adquirió Betfair Colombia y su licencia, cuya aprobación de Coljuegos "lasts until 2025". — [iGaming Business – Stake acquires Betfair Colombia](https://igamingbusiness.com/strategy/ma/stake-acquires-betfair-colombia/)
- En Brasil, Betfair Brasil S.A. está autorizada por la SPA y ofrece Sports, **Exchange** y Casino. Los números de portaria difieren entre fuentes (0014/2024 vs SPA/MF nº 248 de 7-feb-2025). — [UAI](https://www.uai.com.br/apostas/betfair); [Lance](https://www.lance.com.br/sites-de-apostas/betfair-e-confiavel.html) (reseñas comerciales)
- No se encontró la lista oficial de "prohibited territories" de Betfair.com: la página de T&C devolvió 403. Sitios de terceros mencionan bloqueos en Canadá, China, Francia, EAU y gran parte de Oriente Medio, sin citar a Venezuela ni a Colombia. — [pm.wiki](https://pm.wiki/compare/betfair-exchange-vs-taydex) (poco fiable)

**Pinnacle**
- En feb-2025 Coljuegos bloqueó 409 portales de 1Win (141), **1XBET (242)**, BBRBET (19) y **Pinnacle (3)**, que "operaban desde el exterior sin autorización", y anunció más dominios por bloquear. — [SoloAzar](https://soloazar.com/en/category/other/coljuegos-intensifies-actions-to-block-illegal-websites-of-the-1win-1xbet-and-pinnacle-brands)
- No se encontró la lista oficial de países restringidos de Pinnacle (ni para VE ni para CO).

**Smarkets / Matchbook / Betdaq**
- Smarkets: Reino Unido como mercado principal, algunos países europeos y EE. UU. (CO, IN vía SBK). Licencia MGA/CRP/162/2008; busca licencia DCM de la CFTC. — [pm.wiki](https://pm.wiki/zh/compare/betfair-exchange-vs-smarkets) (secundaria). La lista de exclusión del casino de Smarkets (Malta) no menciona VE ni CO. — [StatsDrone](https://statsdrone.com/affiliate-programs/smarkets-affiliates) (secundaria)
- Matchbook: no se halló lista de países restringidos. La única pista es la comisión del 4 % para clientes fuera de UK/IE, lo que implica que acepta algunos no residentes en UK. — [NinjaBet forum](https://ninjabetgermany.nodebb.com/post/451)
- Betdaq: las promos son solo para UK e Irlanda; no se halló lista de países. — [Football Ground Guide](https://footballgroundguide.com/betting/betdaq)

**Coljuegos (Colombia)**
- Coljuegos ha emitido 46.228 órdenes de bloqueo desde 2022 (55.658 en toda su historia) y pide apostar solo con los "15 operadores autorizados" (3-jul-2026). — [Focus Gaming News](https://focusgn.com/latinoamerica/coljuegos-emitio-mas-de-46-000-ordenes-de-bloqueo-contra-sitios-de-apuestas-ilegales)
- Con la Circular 017 de 2025 de MinTIC, Coljuegos puede ordenar bloqueos directamente a los ISP. — [Sigma](https://sigma.world/es/news/coljuegos-agiliza-el-bloqueo-de-sitios-no-autorizados/)
- La operación no autorizada de juegos de suerte y azar es delito (art. 312 del Código Penal); se pidió a Meta y a la Policía bloquear 289 perfiles de influencers. — [Xataka Colombia](https://www.xataka.com.co/redes-sociales/gobierno-solicita-a-meta-bloquear-289-perfiles-influencers-colombia-promocionar-juegos-azar-ilegales); [Public Gaming](https://publicgaming.com/news-categories-m/legal/13893-colombias-gambling-regulator-blocks-10-000-websites-social-media-profiles-offering-illegal-betting)
- La nota de Focus GN no indica que se sancione a los apostadores: el foco legal está en los operadores. — [Focus Gaming News](https://focusgn.com/latinoamerica/coljuegos-emitio-mas-de-46-000-ordenes-de-bloqueo-contra-sitios-de-apuestas-ilegales)

**Venues de EE. UU.**
- ProphetX es un DCM de la CFTC solo de deportes, en todo EE. UU. desde jun-2026 ([Covers](https://www.covers.com/industry/prophetx-launches-prediction-markets-in-us-one-week-after-cftc-approval-june-18-2026)). BetOpenly opera en 45 estados ([podcast](https://podcastrex.com/shows/the-betting-startups-podcast/ep-167-reduced-juice-for-all-w-gino-donati-from-betopenly)). Novig se fondea en USD fiat y es DCM ([Coinpedia](https://coinpedia.org/top-10/10-prediction-market-platforms-worth-knowing-in-2026/)). InGame señala que en EE. UU. las casas y los mercados de predicción exigen número de Seguro Social ([InGame](https://www.ingame.com/?p=48634)).

### Inferences
- **Residente en Venezuela**: Kalshi queda descartado explícitamente. Para el resto de venues no hay evidencia oficial a favor; las sanciones de EE. UU. sobre Venezuela hacen probable la exclusión en venues ligados a EE. UU. (inferencia).
- **Residente en Colombia**: Kalshi aparece como elegible en las fuentes disponibles, aunque con fricciones reportadas en el alta. Ningún exchange estilo Betfair, ni Pinnacle, figura entre los 15 autorizados por Coljuegos según lo hallado. Usarlos desde Colombia supondría tratar con operadores no autorizados que Coljuegos bloquea (Pinnacle, y también 1xBet, que es la fuente actual del usuario).
- Los venues de EE. UU. (ProphetX, Novig, Sporttrade, BetOpenly) requieren residencia y SSN estadounidenses en la práctica: no son opciones.
- La elegibilidad la determinan la residencia y el KYC de la cuenta, no la ubicación del servidor. Un VPS en Finlandia podría chocar con controles por IP de algunos venues; esto debe consultarse con el soporte de cada uno y no "resolverse" con técnicas de evasión.

### Gaps
- Faltan las listas oficiales de territorios restringidos de Betfair.com (Exchange internacional), Pinnacle, Smarkets, Matchbook y Betdaq; las páginas primarias bloquearon el acceso (403) o no se encontraron.
- No se confirmó si la licencia de Coljuegos de Betfair/Stake se renovó después de 2025, ni si incluyó alguna vez el producto Exchange. El listado vigente de operadores autorizados está en coljuegos.gov.co y no se consultó directamente.
- Una nota de jun-2026 indica que el Tesoro de EE. UU. relajó algunas licencias relativas a Venezuela ([Rane](https://worldview.ranenetwork.com/content/situational-report/2026-06-11/u-s-venezuela-u-s-treasury-eases-restrictions-on-venezuela-s-licenses)); no se verificó que haya cambiado la lista de Kalshi.
- No se confirmó si los usuarios internacionales de Kalshi pueden operar todos los contratos deportivos.

## 5. Reglas sobre bots/automatización y arbitraje (incluye white labels tipo Orbit)

### Takeaway
Los exchanges con API oficial (Betfair, Matchbook, Kalshi, Smarkets) admiten la automatización por diseño, pero la limitan por uso personal frente a comercial (Betfair), por cuotas de peticiones y por cargos. No se encontraron cláusulas que prohíban el arbitraje en los exchanges; el "impuesto" a los ganadores en Betfair es el Expert Fee. Los white labels asiáticos de liquidez Betfair (Orbit, Piwi247, etc.) funcionan vía agentes o brokers y sin API oficial para el usuario. En los venues de EE. UU. sin API, raspar los endpoints internos va contra los ToS.

### Cited Findings
- **Betfair**: uso personal; el uso comercial requiere aprobación y "Unauthorised commercial usage will be identified & blocked"; se aplican cargos por transacción; "Make the minimal number of transactions/changes possible" (Best Practice). — [Betfair Developer Docs](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=title~%22Application%20Keys%22&expand=body.view); [Betfair Developer Docs – límites](https://betfair-developer-docs.atlassian.net/wiki/rest/api/content/search?cql=text~%22transaction%20charge%22%20OR%20title~%22Data%20Request%20Limits%22%20OR%20title~%22Market%20Data%20Request%20Limits%22&expand=body.view&limit=5)
- **Betfair Expert Fee**: 20–40 % sobre el beneficio bruto anual por encima de £25k. — [Racing Post](https://racingpost.com/news/britain/betfair-exchange-to-introduce-new-commission-system-for-2025-as-premium-charge-is-dropped-a7wbg0v4GCAJ)
- **Matchbook**: no menciona bots por nombre. Prohíbe eludir los límites y usar la API para "benchmarking or competitive purposes", y puede suspender o terminar el acceso por incumplimientos persistentes. — [Matchbook Fair Usage Policy](https://developers.matchbook.com/docs/fair-usage-policy)
- **Betdaq**: los usuarios de API, RDT o software de trading quedan excluidos de las promos de 0 %. — [Football Ground Guide](https://footballgroundguide.com/betting/betdaq)
- **Kalshi**: los niveles de límite aumentan automáticamente con la cuota de volumen y hay acceso FIX, señal de que el trading algorítmico está soportado. — [Kalshi Docs – Rate limits](https://docs.kalshi.com/getting_started/rate_limits)
- **Smarkets**: ha promovido activamente la construcción de bots de trading (taller de EuroPython). — [EuroPython 2018](https://blog.europython.eu/europython-2018-introducing-smarkets/)
- **ProphetX**: "scraping its internal endpoints is fragile and against its terms of service" (según un vendedor de datos). — [OddsPapi](https://oddspapi.io/blog/?p=2839)
- **Orbit Exchange y similares**: Orbit es un white label de Betfair; el broker BFB247 dice que su exchange funciona sobre Orbit. — [Sumamarka – Orbit review](https://www.sumamarka.org/?p=13046)
- Modelo de agentes: el cliente deposita con un agente de Hong Kong o Macao, recibe una cuenta con línea de crédito y apuesta contra la liquidez del exchange (4casters, Fair999, Orbit, Piwi247, Sharp Exchange), conectada a la escalera de Betfair. Fuente: vendedor de datos, no neutral. — [OddsPapi – Asian Exchange API](https://oddspapi.io/blog/?p=2851)
- Un foro reporta que Orbit dejó de aceptar apuestas a través de un agente y que apareció "Sharp Exchange" en su portal (anecdótico). — [NinjaBet forum](https://ninjabetgermany.nodebb.com/post/50023)
- Los exchanges de agentes "are often unregulated". — [síntesis de resultados de búsqueda sobre Orbit/brokers](https://oddspapi.io/blog/?p=2851)

### Inferences
- Usar Orbit o Piwi247 vía brokers asiáticos supone depender de un intermediario a menudo no regulado y sin API oficial para el usuario final. Encaja mal con "bots vía API oficial" y añade riesgo de contraparte.
- No se encontraron prohibiciones explícitas de arbitraje en los exchanges. El riesgo práctico es económico (Expert Fee, cargos por transacción, coste por GET) más que de cierre de cuenta, a diferencia de las casas tradicionales que limitan a los ganadores (inferencia general, no verificada por venue).

### Gaps
- No se leyeron las cláusulas de los T&C de Betfair, Smarkets ni Matchbook sobre "software automatizado" o "arbitraje" (403).
- No se hallaron los términos de API de Novig ni de ProphetX para market makers.

## 6. Evidencia de diferencias de precio / arbitraje frente a Polymarket

### Takeaway
La evidencia más concreta es la del par Kalshi–Polymarket en deportes. Los mercados se emparejan fácilmente (~98 %), pero los gaps son pequeños y efímeros: mediana de unos 9 s, el 96 % se cierra en menos de 30 s, y en un partido entero el total neto fue de solo 439 USD frente a 20,8 M USD negociados. Las comisiones combinadas suelen superar el spread bruto. No se encontró ningún estudio sistemático Betfair vs Polymarket en deportes.

### Cited Findings
- Un desarrollador construyó un emparejador Kalshi–Polymarket que cubre ~98 % de los mercados de deportes y e-sports. En 24 h registró 870 gaps entre venues, con una duración mediana de ~9 s; el 96 % se cerró en menos de 30 s. En Argentina vs Egipto todas las oscilaciones (incluida la de una jugada VAR polémica) se cerraron en ~45 s, y el arbitraje total neto de comisiones fue de 439 USD, frente a ~20,8 M USD negociados en Polymarket y 13,8 M USD de open interest en Kalshi. Fecha: 8 de julio (probablemente 2026). — [DEV Community](https://dev.to/michaelmustopo/built-a-prediction-market-arbitrage-no-sizable-arbitrage-found-36cf)
- Análisis del Mundial: un spread bruto de $0,008 por par de acciones frente a $0,015 de comisiones taker combinadas. Polymarket cobraba entonces en deportes 3/7 de la tasa de Kalshi. — [pm.wiki – Polymarket–Kalshi arbitrage](https://pm.wiki/uk/learn/polymarket-kalshi-arbitrage)
- Los criterios de resolución difieren a veces entre venues, así que un contrato puede pagar YES en Kalshi y no en Polymarket. — [pm.wiki](https://pm.wiki/uk/learn/polymarket-kalshi-arbitrage) / [newyorkcityservers](https://newyorkcityservers.com/blog/prediction-market-arbitrage-guide) (no se verificó cuál de las dos lo dice)
- arXiv 2508.03474, "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets" (IMDEA): abr-2024 a abr-2025, ~86 M de apuestas, >40 M USD de beneficios de arbitraje extraídos. Un resumen dice que en deportes las oportunidades eran más frecuentes y en política los spreads mayores. Solo se vio vía resúmenes secundarios. — [quantcha / resúmenes](https://quantcha.com/news/?p=606)
- Un paper de SSRN ("Price Discovery and Trading in Prediction Markets") concluiría que Polymarket lidera el descubrimiento de precios y Kalshi se retrasa minutos. No se verificó en el original. — [pm.wiki](https://pm.wiki/uk/learn/polymarket-kalshi-arbitrage) (vía snippet)
- odds-api.io usa Betfair Exchange, SingBet y Polymarket como referencias "sharp" tras la salida de Pinnacle. Indica que se consideran comparables, pero no cuantifica diferencias. — [odds-api.io blog](https://odds-api.io/blog)
- Usuarios de foros que operan en ambos (Polymarket y Kalshi) comparan comisiones, liquidez y cuotas de forma anecdótica. — [BlockEden forum](https://blockeden.xyz/forum/t/i-trade-both-polymarket-and-kalshi-here-is-my-honest-comparison-of-fees-liquidity-ux-and-which-one-actually-has-better-odds/418)

### Inferences
- Para un escáner como el del usuario, el par Kalshi–Polymarket sí produce señales, pero la ventana de ejecución (segundos) exige WebSocket autenticado en Kalshi y ejecución casi simultánea. Desde Finlandia, con latencia hacia los servidores de EE. UU., eso es exigente (inferencia).
- Según mi conocimiento previo, el estudio de IMDEA (arXiv 2508.03474) analiza sobre todo arbitraje *dentro* de Polymarket (rebalanceo y combinatorio), no entre venues. Por eso su cifra de 40 M USD no debe leerse como evidencia de arbitraje Polymarket–exchanges. No se verificó en esta sesión.
- Los gaps de Betfair frente a Polymarket probablemente existen en mercados con menos liquidez en Polymarket y por desajustes de reglas (p. ej., 1X2 frente a binarios, prórroga, push/void), pero no hay datos publicados (inferencia).

### Gaps
- No se encontró ningún estudio o comparación publicada de precios Betfair (o Smarkets/Matchbook/Pinnacle) vs Polymarket en deportes.
- No se leyó directamente el paper arXiv 2508.03474 ni el de SSRN; sus cifras específicas de deportes no están verificadas.
- No hay datos de gaps ProphetX/Novig vs Polymarket.
