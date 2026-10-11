# Casas cripto/offshore y colombianas: API oficial, lectura gratuita de cuotas y aptitud para surebets contra Polymarket (VE/CO, octubre 2026)

Contexto del encargo: el usuario ya lee el feed JSON público de 1xBet y verificó que 888starz, Melbet, Betwinner, 22Bet y Megapari sirven el mismo feed con las mismas cuotas. Tiene cuentas en 1xBet, Stake, 888starz, Rollbit y CoinPoker, y apuesta desde Venezuela y Cúcuta (Colombia). Stake devuelve 403 de Cloudflare a scripts. Solo cuenta el acceso oficial o documentado; nada de saltarse protecciones anti-bot, geobloqueos ni KYC.

Fecha de corte: 10-oct-2026. Los datos viejos se marcan con [DESACTUALIZADO?].

---

## 1. ¿Qué casas tienen API oficial para cuotas y/o colocación de apuestas? (alta, claves, límites, documentación)

### Takeaway
De todas las casas de la lista, la única con una API oficial, pública y documentada para leer cuotas y para colocar apuestas es **Cloudbet** (Feed + Trading + Account, con clave `X-API-Key`). Para Stake, BC.Game, Sportsbet.io, Rollbit, Roobet, Thunderpick, la familia 1xBet y las casas con licencia Coljuegos no encontré ninguna API oficial para jugadores. Pinnacle cerró su API al público el 23-jul-2025.

### Cited Findings

**Cloudbet (Curaçao; Halcyon Super Holdings B.V.)**
- Cloudbet publica tres APIs: **Feed** (cuotas), **Trading** (apostar sobre los mercados del Feed) y **Account** (divisas y saldos). Están documentadas en un sitio público y en GitHub, con definiciones protobuf v3, archivos Go generados, un ejemplo `api-sample.js` y foro de dudas en GitHub Discussions. La lista completa de deportes y mercados está en un gist enlazado. — [GitHub Cloudbet/docs](https://github.com/Cloudbet/docs)
- Hay dos tipos de clave:
  - **Trading API Key**: es para apostar por programa. Se obtiene depositando el equivalente a 10 EUR y generándola en `cloudbet.com/en/player/api`. La wiki advierte que rotar claves con demasiada frecuencia tiene penalización, sin decir cuál.
  - **Affiliate API Key**: es para consumir cuotas sin apostar. Se genera en la cuenta de afiliado, en la página "Affiliate API Token". — [Cloudbet wiki: API](https://cloudbet.github.io/wiki/en/docs/sports/api/)
- Con la clave de afiliado, las respuestas del Feed pueden estar cacheadas y llegar **hasta 1 minuto tarde**. Con la clave de Trading los datos son en tiempo real. — [Cloudbet wiki: API](https://cloudbet.github.io/wiki/en/docs/sports/api/)
- La documentación Swagger/OpenAPI está en `https://www.cloudbet.com/api/`, donde se pueden probar los endpoints del Feed con una clave de afiliado. La wiki tiene secciones Account, Feed, Trading y "Trading B2B" (fijar cuotas propias a partir de los datos suministrados), además de subpáginas de mercados, ejemplos, hándicap, "margin adjustments", **integración MCP** y fondos de prueba. La página se modificó por última vez el 22-may-2024 [DESACTUALIZADO? en parte]. — [Cloudbet wiki: API](https://cloudbet.github.io/wiki/en/docs/sports/api/)
- Datos técnicos:
  - Base URL: `https://sports-api.cloudbet.com`. Cabeceras: `X-API-Key`, `accept: application/json` y `Content-Type: application/json`.
  - Endpoints de Feed: `GET /pub/v2/odds/sports`, `GET /pub/v2/odds/competitions/{competitionKey}?markets=...` y `GET /pub/v2/odds/events/{eventId}`.
  - Endpoints de Account: `GET /pub/v1/account/currencies` y `GET /pub/v1/account/currencies/{currency}/balance`.
  - Endpoints de Trading: `POST /pub/v3/bets/place`, `POST /pub/v3/bets/{referenceId}/status` y `POST /pub/v3/bets/history`. — [Cloudbet wiki: Examples](https://cloudbet.github.io/wiki/en/docs/sports/api/examples/)
- Flujo de una apuesta:
  - Cada apuesta lleva un `referenceId` UUID único y los campos `acceptPriceChange` (en el ejemplo `"BETTER"`), `currency`, `eventId`, `marketUrl`, `price` y `stake`.
  - La respuesta puede quedar en `PENDING_ACCEPTANCE`, así que hay que consultar el estado.
  - Si la rechazan, puede venir un stake y un precio recomendados. Se reintenta con un `referenceId` nuevo.
  - El precio aceptado puede variar un poco: en el ejemplo se pidió 1.65 y se obtuvo 1.652.
  - `PLAY_EUR` es una moneda de prueba que se habilita a pedido. El `minStake` del ejemplo es 0.0001. — [Cloudbet wiki: Examples](https://cloudbet.github.io/wiki/en/docs/sports/api/examples/)
- Límites de uso. Vienen de una copia de la documentación antigua de Cloudbet [DESACTUALIZADO?: según el resumen de la búsqueda, unos ~1500 días de antigüedad]:
  - "Bet endpoint has a rate limit of 1 Request per second".
  - Si la tasa de rechazo es >75% en las últimas 100 apuestas, la cuenta se marca por "abusive behaviour".
  - "You may get blocked for up to 7 days for integrity checks".
  - No figura ningún límite numérico para el Feed. — [GitHub juancarlospaco/cloudbet (README, sección Limits)](https://github.com/juancarlospaco/cloudbet)
- Un directorio de APIs describe el Feed como una superficie de solo lectura de 7 endpoints bajo `/v2/odds`, que responde en JSON o protobuf binario. Según ese directorio, Trading y Affiliate usan el mismo host y claves en formato JWT. — [Jentic: cloudbet.com](https://jentic.com/apis/cloudbet.com.md)
- Hay contradicción: OddsPapi afirma que ninguno de los books cripto que cubre (entre ellos Cloudbet) tiene API pública. — [OddsPapi, Crypto Bookmaker API (7-abr-2026)](https://oddspapi.io/blog/?p=2812). Lo contradicen la documentación oficial de Cloudbet ([GitHub Cloudbet/docs](https://github.com/Cloudbet/docs)) y su blog con tutoriales de API ([Cloudbet blog: tutorial Golang](https://www.cloudbet.com/en/blog/sports/sports-betting-api-tutorial-with-golang/)).

**Stake (stake.com / stake.com.co)**
- Proveedores de datos afirman que Stake no tiene API pública de cuotas: no hay portal de desarrolladores, ni sistema de claves, ni documentación. Ojo: son vendedores con interés comercial. — [SharpAPI: Stake](https://sharpapi.io/sportsbooks/stake-odds-api); [OddsPapi: Stake](https://oddspapi.io/sportsbooks/stake)
- Un escáner de "preparación para agentes" informó que Stake bloqueó su escáner con un desafío anti-bot o WAF. Encaja con el 403 de Cloudflare que ve el usuario. — [StartupHub agent-ready index: stake.com](https://www.startuphub.ai/agent-ready-index/stake-com)
- Hay un paquete PyPI no oficial que dice usar la "API oficial" para transferencias y saldo y que presume de "evitar detección". No es documentación de Stake y va contra el criterio de acceso legítimo. — [PyPI stake-api](https://pypi.org/project/stake-api/)

**BC.Game, Roobet, Rollbit (motor Betby)**
- Betby se presenta como proveedor de BC.Game, Roobet, Rollbit y CSGOEmpire. La página no tiene fecha. — [FocusGN: partner Betby](https://focusgn.com/partners/betby)
- Betby suministra la solución de sportsbook completa a BC.Game desde abril de 2022 [DESACTUALIZADO?]. — [SBC News, 28-abr-2022](https://sbcnews.co.uk/sportsbook/2022/04/28/betby-to-power-launch-of-bc-game-crypto-sportsbook/)
- Betby da soporte al sportsbook de Roobet, con foco en Latinoamérica, desde noviembre de 2022 [DESACTUALIZADO?]. — [SBC News, 10-nov-2022](https://sbcnews.co.uk/sportsbook/2022/11/10/roobet-betby-latam-growth/)
- Rollbit: solo aparecen agregadores de terceros (OddsPapi). No encontré documentación oficial para desarrolladores. — [OddsPapi: Rollbit](https://oddspapi.io/sportsbooks/rollbit)

**Sportsbet.io, Thunderpick**
- No encontré API oficial ni documentación para desarrolladores de ninguna de las dos. Para Sportsbet.io solo aparece un revendedor de datos. — [SportsGameOdds: Sportsbet](https://sportsgameodds.com/bookmakers/sportsbet-odds-api)
- Thunderpick opera bajo Paloma Media B.V. con licencia de Curaçao y es un book enfocado en esports. — [Parameter.io: Thunderpick review](https://parameter.io/thunderpick-review/)
- Thunderpick tiene una alianza con Oddin.gg, proveedor B2B de cuotas de esports, de septiembre de 2025. Solo leí el titular. — [SBC News, 15-sep-2025](https://sbcnews.co.uk/europe/2025/09/15/oddin-gg-thunderpick/)

**Familia 1xBet**
- OddsPapi afirma que 1xBet no ofrece una API pública de cuotas. El feed JSON que usa el usuario es el que consume la propia web: es público, pero no está documentado como API. — [OddsPapi, Crypto Bookmaker API](https://oddspapi.io/blog/?p=2812)

**Pinnacle**
- Pinnacle cerró su suite de APIs al público general el **23-jul-2025**. Ahora solo ofrece "bespoke data services" para apostadores selectos de alto valor y para socios comerciales. Se solicita escribiendo a `api@pinnacle.com`. — [GitHub pinnacleapi-documentation](https://github.com/pinnacleapi/pinnacleapi-documentation)
- Desde entonces el mercado depende de revendedores como SharpAPI y Odds-API.io. — [Odds-API.io: Pinnacle API shutdown alternatives](https://odds-api.io/blog/pinnacle-api-shutdown-alternatives); [DEV Community](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)

**Casas con licencia Coljuegos (Wplay, Betplay, Rushbet, Codere, Betano, etc.)**
- No encontré ninguna API oficial para jugadores en ninguna de ellas (ver Gaps).

### Inferences
- Cloudbet es el único candidato de la lista que permite automatizar **legítimamente** las dos patas: leer cuotas y apostar. Además tiene MCP oficial, lo que facilita integrarlo con agentes.
- La regla de rechazos (>75% de las últimas 100 apuestas) castiga a los bots de surebet que piden precios ya movidos. Conviene usar `acceptPriceChange` con criterio y no reintentar en bucle.
- El límite de 1 req/s en `/bets/place` sobra para surebets pre-partido.
- Stake, BC.Game, Rollbit, Roobet, Sportsbet.io, Thunderpick y las casas colombianas quedan como patas **manuales**, o solo legibles a través de agregadores de pago.
- BC.Game, Roobet y Rollbit comparten motor (Betby). Probablemente tienen precios correlacionados, igual que pasa con los clones de 1xBet. Esto **no está verificado** con cuotas reales.

### Gaps
- No encontré límites de tasa actuales del Feed de Cloudbet. El dato de 1 req/s en apuestas sale de documentación antigua. Para confirmarlo hay que mirar la Swagger viva en cloudbet.com/api, que no se pudo leer porque es una SPA.
- No sé si la clave de afiliado exige tener tráfico o aprobación como afiliado, ni si la cuenta de afiliado de Cloudbet acepta residentes de VE/CO.
- No encontré si Cloudbet cobra alguna comisión por usar la API. Nada indica que la haya.
- No encontré datos de Betpanda, Bitsler ni Mostbet (API, motor, cuotas).
- No sé si Wplay, Betplay, Rushbet o Codere usan proveedores B2B (Kambi, Altenar, etc.) con endpoints públicos documentados. Los endpoints no documentados de esos proveedores no cuentan como acceso oficial.

---

## 2. ¿Se pueden leer las cuotas gratis, sin login, mediante un feed oficial o documentado?

### Takeaway
No encontré ningún feed oficial, documentado y sin login en ninguna de las casas. Lo más parecido es **Cloudbet**: es gratis, pero pide clave (de afiliado, con hasta 1 minuto de retraso, o de Trading, en tiempo real tras depositar 10 EUR). El feed de la familia 1xBet es público pero no está documentado como API. Stake bloquea scripts con Cloudflare y no ofrece alternativa oficial.

### Cited Findings
- Cloudbet: todo el Feed requiere `X-API-Key`, sea de Trading o de afiliado. — [Cloudbet wiki: Examples](https://cloudbet.github.io/wiki/en/docs/sports/api/examples/)
- Con clave de afiliado, las cuotas pueden ir hasta 1 minuto atrasadas. — [Cloudbet wiki: API](https://cloudbet.github.io/wiki/en/docs/sports/api/)
- Para Stake, el acceso a cuotas que se publicita pasa por agregadores de pago como OddsPapi o SharpAPI. Los propios agregadores dicen que extraer los endpoints internos de Stake viola sus términos; es una afirmación de marketing y no encontré la cláusula. — [SharpAPI: Stake](https://sharpapi.io/sportsbooks/stake-odds-api); [OddsPapi: Stake](https://oddspapi.io/sportsbooks/stake)
- OddsPapi vende acceso REST a 1xBet, Stake, BC.Game, Cloudbet, Duelbits, Rollbit, Roobet, Vave, Mystake y Stake Brasil, y tiene un plan gratuito. — [OddsPapi, Crypto Bookmaker API](https://oddspapi.io/blog/?p=2812); [OddsPapi: Rollbit](https://oddspapi.io/sportsbooks/rollbit)
- Pinnacle ya no ofrece lectura pública desde el 23-jul-2025. — [GitHub pinnacleapi-documentation](https://github.com/pinnacleapi/pinnacleapi-documentation)

### Inferences
- Para el escáner, la combinación gratuita y legítima más sólida es esta: el feed de 1xBet que el usuario ya lee (cubre toda la familia de clones), más Cloudbet vía API con clave, más Polymarket.
- Para Stake y los books de Betby, la única vía legítima sin raspar es un agregador de pago, o comprobar a mano antes de apostar.
- El retraso de hasta 60 s de la clave de afiliado sirve para pre-partido, no para en vivo.

### Gaps
- No verifiqué los términos ni la licencia de uso de datos del plan gratuito de OddsPapi (cuota de peticiones, uso comercial).
- No sé si alguna casa colombiana con licencia publica cuotas en formato abierto.

---

## 3. Márgenes típicos (fútbol, tenis, esports): ¿casas blandas o afiladas?

### Takeaway
Los márgenes publicados para books cripto rondan el 3–6% en mercados principales. Cloudbet tiene promociones puntuales de margen cero en eventos grandes. Las fuentes son mayormente de afiliados y se contradicen. No hay datos sólidos de tenis, ni de BC.Game, Sportsbet.io, Rollbit, Roobet, Betpanda, Bitsler o Mostbet. Pinnacle sigue siendo la referencia afilada, con un overround del 3.86% en un ejemplo de 2026.

### Cited Findings
- **Cloudbet**: márgenes estándar del 3.45% al 5.1% en deportes principales y ~3.8% en hándicap asiático. Promos de margen cero periódicas, no permanentes (Champions, Grand Slams, NFL, EPL, NBA, etc.). Es una reseña de afiliado. — [Tech-Insider: Cloudbet review 2026](https://tech-insider.org/crypto-casino/platforms/cloudbet-review/)
- Una guía de arbitraje cripto presenta a Cloudbet con "zero margin odds and no maximum bet limits on any market". Es promocional y poco fiable. — [DeucesCracked: crypto arbitrage](https://www.deucescracked.com/crypto-gambling/sports-betting/arbitrage)
- En una muestra del Mundial, CryptoSlate vio cuotas 1X2 de Cloudbet peores que las de Stake, Dexsport y Bet25, y describe a Stake con 1X2 "más afilado" que Cloudbet. La página tiene enlaces de afiliado. — [CryptoSlate: Cloudbet vs Stake](https://cryptoslate.com/?p=541209)
- **Stake**: márgenes "low-to-mid single digits" en mercados principales, sin cifra exacta. **Thunderpick**: "thinner margins" fuera de esports, solo cualitativo. Nota del 28-jul-2026 que resume una pieza de cryptodaily, posiblemente patrocinada. — [CoinAlertNews, 28-jul-2026](https://coinalertnews.com/news/2026/07/28/crypto-sportsbook-margin-comparison); [CryptoDaily (sponsored)](https://cryptodaily.co.uk/2026/07/comparing-crypto-sportsbook-odds-across-five-platforms)
- Ejemplo concreto: en la 1. Division danesa, cuota visitante con Pinnacle 3.38, 1xBet 3.26, BC.Game 3.20 y Stake 3.10. Los books cripto quedan por debajo de Pinnacle en ese resultado. OddsPapi no los clasifica como blandos ni afilados, pero dice que 1xBet y Stake aceptan stakes altos "con restricciones mínimas". — [OddsPapi, Crypto Bookmaker API (7-abr-2026)](https://oddspapi.io/blog/?p=2812)
- Overrounds medidos en una pizarra de Euroliga (1-oct-2026): Pinnacle 3.86% y bet365 7.12%. Duelbits cotizaba a Real Madrid a 1.96, ~20% por encima de la mediana, lo que generaba un "arb" falso del 8%. — [OddsPapi, Polymarket Arbitrage](https://oddspapi.io/blog/?p=2536)
- Familia 1xBet: hay reportes antiguos [DESACTUALIZADO?] de que los socios white-label (Melbet, Betwinner) tenían líneas y cuotas propias, porque "diferentes analistas" trabajan cada línea. — [NewsDay Zimbabwe](https://newsday.co.zw/business/article/21583/is-there-any-connection-between-1xbet-and-melbet-bookmakers). Esto queda superado por la verificación del usuario (oct-2026): feed y cuotas idénticos en 888starz, Melbet, Betwinner, 22Bet y Megapari.

### Inferences
- Los books cripto se parecen más a casas "blandas con límites altos" que a casas afiladas. Sirven para arbitrar contra Polymarket cuando Polymarket se separa del consenso, pero sus márgenes del 3–6% dejan poco hueco para surebets puras.
- Las promos de margen cero de Cloudbet en eventos grandes son las ventanas con más probabilidad de surebet contra Polymarket.
- Un outlier extremo, como el +20% de Duelbits, es más probablemente un error palpable que una surebet real (ver sección 4).

### Gaps
- No hay mediciones independientes de overround por deporte (fútbol, tenis, esports) para ninguna de estas casas. El usuario puede medirlas con sus propios datos.
- Otra guía citada en la búsqueda daba ~5.9% de margen para Cloudbet en mercados populares, pero no pude atribuir la URL exacta.
- No encontré datos de márgenes de Betano, Wplay, Betplay, Rushbet ni Codere.

---

## 4. Límites y actitud hacia arbitrajistas (restricciones, anulaciones, "error palpable")

### Takeaway
Ninguna casa publica una política "pro-arbitraje". Cloudbet se anuncia con límites altos, pero hay reseñas de usuarios limitados y de cuentas congeladas por "integridad". Además, su API penaliza tasas altas de rechazo. 1xBet anula ganancias sobre todo por multicuenta. Los clones de 1xBet pueden detectar surebets cruzadas entre sus marcas.

### Cited Findings
- Cloudbet:
  - Varios usuarios en Trustpilot dicen haber sido limitados tras apostar de forma ganadora y legítima, pese a la publicidad de "sin límites".
  - Cloudbet responde que los límites varían por evento y mercado y que su equipo de trading los revisa continuamente. — [Trustpilot AU p.6](https://au.trustpilot.com/review/www.cloudbet.com?page=6)
  - Un usuario cuenta que su cuenta se pausó por una "betting integrity investigation" que, según le dijeron, podía tardar hasta 6 meses.
  - A otro le negaron subir el límite por "breaches of rules". — [Trustpilot IE p.7](https://ie.trustpilot.com/review/www.cloudbet.com?page=7)
  - Hay un caso de cuenta cerrada con saldo confiscado por presunta multicuenta y uso de VPN. Es solo la versión del usuario. — [Trustpilot NZ p.2](https://nz.trustpilot.com/review/www.cloudbet.com?page=2)
- Cloudbet API: una tasa de rechazo >75% en las últimas 100 apuestas lleva a marcar la cuenta por abuso y a un bloqueo de hasta 7 días por revisión de integridad [doc antigua]. — [GitHub juancarlospaco/cloudbet](https://github.com/juancarlospaco/cloudbet)
- Cloudbet: un artículo patrocinado habla de un tope de retiro de $2,200 diarios sin KYC completo. — [MuddyRiverNews (sponsored)](https://muddyrivernews.com/sponsored-content/cloudbet-vs-moonbet-2026-kyc-traps-vs-no-kyc-crypto-casino/20260413052451/). Otra reseña dice que no hay tope de retiro una vez verificado. — [WorldPokerDeals (31-jul-2026)](https://worldpokerdeals.com/online-casinos/articles/cloudbet-countries-guide)
- 1xBet: el soporte cita cláusulas de sus términos en respuestas a reclamos:
  - 2.26: una cuenta por persona, dirección, correo, IP o medio de pago.
  - 2.27: prohibido re-registrarse.
  - 2.30: cancelación de ganancias en cuentas duplicadas.
  - 2.31: cierre de la cuenta y cancelación de ganancias por incumplimiento.
  - Son citas en páginas de reclamos, no en el documento oficial. — [TuQuejaSuma: 1xBet bloqueo](https://tuquejasuma.com/1xbet/reclamos/1xbet-cuenta-365610725-bloqueada-sin-dar-motivos); [TuQuejaSuma: robo-fraude](https://tuquejasuma.com/1xbet/reclamos/robo-fraude); [TuQuejaSuma: 1xbet-ladrones](https://tuquejasuma.com/1xbet/reclamos/1xbet-ladrones)
- Según un artículo sobre la relación 1xBet–Melbet, cuando se apuestan resultados opuestos en sitios socios, las compañías pueden reconocer la surebet y el "corredor". [DESACTUALIZADO?] — [NewsDay Zimbabwe](https://newsday.co.zw/business/article/21583/is-there-any-connection-between-1xbet-and-melbet-bookmakers)
- Contexto general: el arbitraje y el abuso de bonos suelen violar los términos y llevar a restricciones. Los operadores usan etiquetas vagas como "irregular" o "suspicious". — [GamblingHarm](https://gamblingharm.org/do-online-sportsbooks-limit-or-ban-winners/); [Non-League Football Paper](https://thenonleaguefootballpaper.com/guest-posts/443379/bookmakers-without-limits-try-your-luck-freely)
- Los books cripto como 1xBet y Stake aceptan stakes altos "con restricciones mínimas", frente a los regulados que "limitan agresivamente". Es afirmación de un vendedor. — [OddsPapi, Crypto Bookmaker API](https://oddspapi.io/blog/?p=2812)

### Inferences
- Dentro de la familia 1xBet, apostar lados opuestos entre clones (por ejemplo 1xBet contra Melbet) es lo más detectable y no aporta nada, porque las cuotas son idénticas. La pata útil frente a Polymarket es **una sola** casa de la familia.
- Con Polymarket como contraparte, la casa solo ve una apuesta "de valor" en su lado, porque la pata en Polymarket le es invisible. Aun así, un patrón ganador sostenido puede acabar en límites, sobre todo si se apuesta solo cuando la casa se separa del consenso.
- Riesgo de "error palpable": las cuotas outlier (como el +20% de Duelbits) son candidatas a anulación. El escáner debería descartar edges anormalmente grandes.

### Gaps
- No encontré el texto oficial vigente de las reglas de "obvious/palpable error" ni de la prohibición de software automatizado en 1xBet, Stake, Cloudbet o Rollbit. Hay que revisar el T&C de cada una directamente.
- No encontré hilos de Reddit, en inglés o en español, con experiencias de arbitraje contra Polymarket en estas casas.
- No encontré si Cloudbet permite oficialmente el uso de su API para arbitraje. Su existencia sugiere que tolera bots, pero no lo dice.

---

## 5. Disponibilidad para Venezuela y Colombia (licencia Coljuegos frente a offshore)

### Takeaway
En Colombia solo es legal operar con las casas de la lista de Coljuegos. A 1-oct-2026 esa lista tiene 14 operadores, **incluido Stake (stake.com.co, contrato 2226)**, Betano, Wplay, Betplay, Rushbet, Codere, Bwin, etc. Cloudbet, 1xBet, BC.Game y el resto de cripto son offshore. En Venezuela el juego online está en un vacío legal; la CNC tiene su propio registro de casinos online locales, y en él no figuran ni 1xBet ni Stake.

### Cited Findings
- **Lista oficial de Coljuegos** (última modificación 01/10/2026):

  | # | Empresa | Marca / dominio | Contrato |
  |---|---|---|---|
  | 1 | Aquila Global Group | Wplay.co | 2182 |
  | 2 | Corredor Empresarial | Betplay.com.co | 1876 |
  | 3 | Colbet | Betsson.co | 1895-B |
  | 4 | E Total Gaming | Zamba.co | 1894 |
  | 5 | Codere Online Colombia | Codere.com.co | 2218 |
  | 6 | Luckia Colombia | Luckia.co | 1932 |
  | 7 | Sportium Apuestas Colombia | Sportium.com.co | 1942 |
  | 8 | Rush Street Interactive Colombia | Rushbet.co | 1972 |
  | 9 | Teclino | Rivalo.co | 1997 |
  | 10 | Games and Betting | Yajuego.co | 2240 |
  | 11 | Bwin Latam | sports.bwin.co | 2229 |
  | 12 | **Stake Colombia S.A.S.** | **Stake.com.co** | **2226** |
  | 13 | Kaizen Gaming Colombia | Betano.co | 2104 |
  | 14 | Global Vitxo | Mryoker.co | C2261 |

  La misma página dice "16 páginas legales", pero solo lista 14. — [Coljuegos: páginas autorizadas](https://www.coljuegos.gov.co/publicaciones/juegosonline_pub)
- Coljuegos publica advertencias sobre páginas de apuestas ilegales y remite a la lista anterior. — [Coljuegos: advertencia sitios ilegales](https://www.coljuegos.gov.co/publicaciones/301824/)
- Con MrYoker (contrato C2261 de 2026, habilitado hasta 2031) los operadores pasaron a ser 15, según SIGMA. Ojo: no coincide con los 14 de la página oficial. — [SIGMA: MrYoker](https://sigma.world/es/news/mryoker-operador-regulado-colombia/)
- En febrero de 2024 la lista incluía Betfair, William Hill, MegApuestas, Aquijuego y Fullreto, que ya no aparecen en 2026 [DESACTUALIZADO]. — [La República, feb-2024](https://www.larepublica.co/internet-economy/casas-de-apuestas-que-hay-en-internet-3808219)
- Las plataformas online colombianas generaron COP 473.023 millones entre enero y agosto de 2026, con el Mundial como impulso. — [SIGMA: recaudo Coljuegos 2026](https://sigma.world/es/news/recaudo-coljuegos-2026-apuestas-mundial/)
- **Stake.com y Colombia**: Next.io incluye a Colombia en la lista de países restringidos de stake.com. — [Next.io](https://next.io/win/stake/available-countries/). The Spike menciona el dominio licenciado stake.com.co. — [The Spike](https://www.thespike.gg/reviews/stake-com/legal). SportsGambler (2026) da Colombia como accesible. — [SportsGambler](https://www.sportsgambler.com/review/stake/legal/)
- **Cloudbet**:
  - WorldPokerDeals (31-jul-2026) marca Colombia y Venezuela como **aceptados**.
  - Su lista de 20 restringidos: Australia, Austria, Bélgica, China/Macao/Hong Kong, Crimea/Donetsk/Luhansk, Cuba, Curaçao, Francia, Alemania, Irán, Lituania, Malta, Myanmar, Países Bajos, Corea del Norte, Singapur, España, Siria, Reino Unido y EE. UU.
  - La ubicación se comprueba al registrarse y al retirar.
  - La misma guía dice que la lista de referencia es la del propio T&C de Cloudbet. — [WorldPokerDeals](https://worldpokerdeals.com/online-casinos/articles/cloudbet-countries-guide)
  - Lo contradice una lista de intercom.help que incluye a Colombia, aunque podría ser de otro operador. — [intercom.help/FJ1](https://intercom.help/FJ1/en/articles/9133419-restricted-countries-list)
- **Venezuela**:
  - La CNC publicó un registro de plataformas autorizadas con 28 casinos online y 2 operadores de bingo (códigos CNC-OL-25). Son empresas locales y no aparecen ni 1xBet ni Stake. — [FocusGN: CNC lista oficial](https://focusgn.com/latinoamerica/la-comision-nacional-de-casinos-de-venezuela-publica-lista-oficial-de-plataformas-de-juego-autorizadas)
  - El juego online en Venezuela está "legalmente indefinido": ni prohibido expresamente ni autorizado. La fuente es comercial. — [Altenar: leyes Venezuela](https://altenar.com/po/blog/gambling-laws-and-regulations-in-venezuela/)
  - El INH censa las casas de apuestas deportivas e hípicas presenciales para sancionar a las ilegales. — [FocusGN: INH censo](https://focusgn.com/latinoamerica/el-instituto-nacional-de-hipodromos-de-venezuela-censa-los-centros-de-apuestas-del-pais)

### Inferences
- Desde Cúcuta, la vía legal para Stake es **stake.com.co**, regulada por Coljuegos. Es probable que stake.com redirija o restrinja a los usuarios colombianos; está por verificar. Una cuenta de stake.com abierta desde Venezuela puede quedar sujeta a otra jurisdicción y otro T&C.
- Cloudbet, 1xBet y sus clones, BC.Game, Rollbit, Roobet, Thunderpick, etc. **no** tienen licencia Coljuegos, así que en Colombia son ilegales y pueden estar bloqueados. No describo formas de esquivar esos bloqueos.
- Para un escáner que opera legalmente desde Colombia, las patas utilizables son las casas Coljuegos, ninguna de las cuales tiene API, más Polymarket. La disponibilidad de Polymarket en Colombia no la cubre esta investigación.

### Gaps
- No pude leer directamente el T&C de Cloudbet, Stake, 1xBet, Rollbit, BC.Game ni Thunderpick con sus listas de países restringidos. Todas las listas citadas son de terceros.
- No encontré si stake.com acepta registros desde Venezuela según su T&C vigente.
- No confirmé si Pinnacle acepta jugadores de VE o CO. No figura en la lista de Coljuegos.
- No hay datos sobre bloqueos por DNS o ISP de Coljuegos a dominios concretos (1xBet, Cloudbet) en 2026.

---

## 6. Familia white-label de 1xBet: ¿qué marcas comparten feed? ¿cambian márgenes o límites?

### Takeaway
1xBet vende plataformas white-label. Melbet está documentada como socio desde 2018, y el usuario ha verificado cuotas idénticas en 888starz, Melbet, Betwinner, 22Bet y Megapari. Para el escáner cuentan como **una sola fuente de precio**. Sobre diferencias de límites por marca no encontré datos.

### Cited Findings
- 1xBet ofrece plataformas white-label a socios y nombra a Melbet.com como uno de ellos. La propiedad no está confirmada oficialmente, pero comparten interfaz, registro, línea, cuotas, bonos y reglas. — [NewsDay Zimbabwe](https://newsday.co.zw/business/article/21583/is-there-any-connection-between-1xbet-and-melbet-bookmakers)
- Titular de SBC News de 2018: "1xBet white label kickstarts Melbet". No pude leer el cuerpo (error 403). — [SBC News, 14-mar-2018](https://sbcnews.co.uk/sportsbook/2018/03/14/1xbet-white-label-kickstarts-melbet/)
- En su momento, Betwinner tenía "line-up and coefficients" distintos de 1xBet [DESACTUALIZADO; contradicho por la verificación del usuario en 2026]. — [NewsDay Zimbabwe](https://newsday.co.zw/business/article/21583/is-there-any-connection-between-1xbet-and-melbet-bookmakers)
- 1xCorp N.V., filial de 1xBet, fue declarada en quiebra por el Tribunal Supremo neerlandés en enero de 2023 y siguió operando. La fuente es un espejo de Wikipedia, así que es débil. — [wiki.riteme.site: 1xBet](https://wiki.riteme.site/wiki/1xbet)

### Inferences
- Como las cuotas son idénticas, añadir más clones al escáner no crea surebets nuevas. Lo que sí aporta son cuentas y límites adicionales para repartir el stake de la misma oportunidad, siempre respetando las reglas de multicuenta de cada marca. Son operadores distintos, pero comparten detección de surebets cruzadas según NewsDay.

### Gaps
- No encontré nada de Linebet, Paripesa, Helabet, 888starz ni Megapari en fuentes externas.
- No encontré si los límites máximos por apuesta o el trato a ganadores difieren entre marcas de la familia.

---

## 7. Evidencia de surebets entre estas casas y Polymarket o exchanges

### Takeaway
Hay evidencia cuantitativa, aunque de un vendedor de datos, de surebets reales pero **pequeñas** entre Polymarket y casas: mediana 0.44% y máximo 1.53% tras filtrar, el 1-oct-2026. No encontré evidencia publicada específica de Cloudbet, Stake o 1xBet contra Polymarket.

### Cited Findings
- Snapshot de OddsPapi del 1-oct-2026, sobre 28 pizarras completas:
  - 2,938 pares brutos por debajo del 100%; tras 6 filtros y deduplicar quedaron **42 arbs distintos en 16 partidos**.
  - Edge mediano 0.44%, máximo 1.53%.
  - Profundidad mediana en el primer nivel del libro de Polymarket: **$4,023**.
  - Polymarket tenía precio en 70 de 75 partidos muestreados, de 7 deportes.
  - Ejemplo: Hapoel Tel-Aviv vs Real Madrid (Euroliga). Polymarket 2.439 para Hapoel, con $4,325 en el primer nivel, y FanDuel 1.74 para Real Madrid: 98.47%, un edge del 1.53%.
  - Los arbs brutos grandes (por ejemplo un 29% contra una orden de $1.15) no se podían llenar.
  - Un arb del 1.22% aparecía 8 veces, una por cada skin de Betano.
  - El feed de `polymarket.us` mostraba otros precios y profundidad cero.
  - El estudio **excluye comisiones**, costes de depósito y tiempos de transferencia.
  - Para no mezclar mercados de tiempo reglamentario con mercados que incluyen prórroga, empareja por `marketId`.
  - El post tiene fecha de 25-mar-2026, pero los datos son del 1-oct-2026; es una inconsistencia del propio post.
  - No menciona a 1xBet, Stake ni Cloudbet. — [OddsPapi: Polymarket Arbitrage](https://oddspapi.io/blog/?p=2536)
- Los vendedores de APIs (SportsGameOdds, SharpAPI, OddsPapi) promocionan el "arbitraje Polymarket vs sportsbooks" como caso de uso, sin resultados auditados. — [SportsGameOdds: prediction market arbitrage](https://sportsgameodds.com/use-cases/prediction-market-arbitrage-api); [SharpAPI: Polymarket API guide](https://sharpapi.io/learn/how-to-get-polymarket-api)

### Inferences
- Con edges medianos por debajo del 0.5%, las comisiones de Polymarket (si las hay en deportes), el spread, el gas o el bridge y la conversión cripto/fiat pueden comerse la mayoría de las surebets. Por eso tiene sentido priorizar la herramienta clv.py del usuario frente a la surebet pura.
- Las skins duplicadas (Betano en el estudio, clones de 1xBet aquí) inflan el número de "oportunidades". El escáner debe deduplicar por grupo de feed.
- La diferencia de reglas de resolución entre Polymarket (on-chain) y la casa (prórroga, abandono, void) es un riesgo real de "lock" que falla.

### Gaps
- No hay estudios independientes de arbitraje Polymarket contra books cripto (Cloudbet, Stake, 1xBet).
- La afirmación de que Polymarket y las casas "divergen un 3–8%" apareció en un resumen de búsqueda de vendedores, sin URL exacta atribuible. No la uso como dato.
- Las comisiones actuales de Polymarket en mercados deportivos no entran en este encargo.
