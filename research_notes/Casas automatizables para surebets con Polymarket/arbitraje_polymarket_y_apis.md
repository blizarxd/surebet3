# Arbitraje y eficiencia de precios Polymarket deportes vs. otras casas, y APIs de cuotas con capa gratuita (estado a octubre 2026)

Notas de investigación. Fecha de corte: 10-oct-2026. Casi todo el material sobre APIs viene de las propias empresas (marketing), así que hay que tomarlo con cautela. Donde un dato es viejo o hay fuentes que se contradicen, lo indico.

---

## 1. ¿En qué segmentos tiene Polymarket más precios desalineados frente a casas sharp (Pinnacle) y casas blandas?

### Takeaway
Polymarket deportes está muy cerca de las casas sharp y de Kalshi en los partidos grandes de EE. UU. Los market makers anclan a Pinnacle, así que contra Pinnacle o Kalshi casi nunca hay surebet después de comisiones. Las brechas que se repiten salen de emparejar Polymarket con casas blandas o regionales (Olimp, 1xBet, BetBoom, Marathon, Duel, FanDuel). Se concentran en eSports (LoL, Dota 2, CS2), en el prematch más que en el live por la parte que se puede ejecutar, y en los minutos finales del in-play. La mayoría son pequeñas (mediana de ~0,4–3%). No encontré estudios específicos sobre tenis ITF ni fútbol de ligas menores.

### Cited Findings
**Polymarket frente a Kalshi y casas sharp (casi sin arbitraje)**
- Un escáner paper-only del 6-sep-2026 encontró 266 partidos activos a la vez en Kalshi y Polymarket. En casi todos, el mejor bid/ask estaba a menos de 1 centavo de distancia, así que no había arbitraje después de comisiones (hacía falta una brecha de ~3 puntos). Solo aparecieron dos oportunidades pequeñas: Mets/Marlins con 27 contratos por $0,23 (0,9%), y una de WNBA al 0,75% que dependía de una línea "stale" de DraftKings. — [GitHub VV1Git/sportstrades](https://github.com/VV1Git/sportstrades)
- En ese mismo escáner, los mercados de predicción se separaban de las líneas de DraftKings sin vig ~1–3 puntos en NFL semana 1 y hasta ~4 puntos en WNBA y MLB. El signo variaba y la brecha cabía dentro del vig de la casa, así que nada era libre de riesgo tras comisiones. — [GitHub VV1Git/sportstrades](https://github.com/VV1Git/sportstrades)
- Un desarrollador que emparejó ~98% de los mercados deportivos y eSports entre Kalshi y Polymarket no encontró arbitraje de tamaño relevante. En Argentina vs Egipto el arbitraje neto total fue de $439, frente a $20,8M de volumen en Polymarket. En una muestra de 24 h hubo 870 brechas, abiertas una mediana de ~9 s; el 96% se cerró en menos de 30 s (post de julio 2026). — [DEV: "no sizable arbitrage found"](https://dev.to/michaelmustopo/built-a-prediction-market-arbitrage-no-sizable-arbitrage-found-36cf)
- En 30 días de datos de eSports (6-sep a 6-oct-2026; 27 venues; ~4,87M arbs registrados), solo el 0,09% de los arbs (4.435) emparejaban Polymarket con Kalshi. La contraparte habitual de los arbs con Polymarket era una casa de apuestas. — [DEV/Dozor: 30 days of esports data](https://dev.to/dozor/polymarket-kalshi-arbitrage-vs-sportsbooks-30-days-of-esports-data-1dc2)
- Un market maker automático entrevistado por Polymarket usa la línea de Pinnacle como base para las cuotas pregame. En un Chiefs, Pinnacle daba un precio justo de ~64,5¢ y él ponía unos $5.000 de bids a 64¢. A 63¢ (≈1,5¢ de edge) pujaba "básicamente ilimitado" porque podía revender en otra casa a 63,5¢. Contó que el flujo minorista empujó el precio de Polymarket mientras la línea de Pinnacle seguía igual. En futuros, algunos equipos cotizaban ~40% por debajo del precio justo de las casas sharp. Son cifras autodeclaradas por un trader anónimo (oct-2025, dato antiguo). — [Polymarket "The Oracle": Meet Your Market Maker](https://news.polymarket.com/p/meet-your-market-maker)
- Ese mismo MM ponía tamaños de hasta ~$25k por partido NFL, ~$5k por partido de college football y hasta ~$300k en juego un domingo de NFL. In-game solo cotizaba en descansos (unos pocos miles por lado) y en el medio tiempo (~$5k). Retiraba órdenes cuando había riesgo de noticias de lesiones (oct-2025). — [Polymarket "The Oracle"](https://news.polymarket.com/p/meet-your-market-maker)
- Una guía de terceros recomienda Pinnacle (PS3838) en cuotas decimales como referencia sharp para deportes en Polymarket y Kalshi. — [ClawArbs: Polymarket value betting](https://clawarbs.com/blog/polymarket-value-betting/)

**Polymarket frente a casas blandas o regionales (aquí está el arbitraje)**
- Pares con más arbs en eSports (sep–oct 2026). Olimp–Polymarket: 135.476 arbs, vida mediana 6,1 s, ROI mediano 2,81%. Duel–Polymarket: 80.260, 4,2 s, 1,96%. **1xBet–Polymarket: 69.719 arbs, 5.437 duraron ≥60 s, vida mediana 4,0 s, ROI mediano 2,96%.** BetBoom–Polymarket: 39.014, 6,4 s, 3,91%. Marathon–Polymarket: 37.755, 6,1 s, 3,47%. Kalshi–Olimp: 25.469, 4,2 s, 2,82%. — [DEV/Dozor](https://dev.to/dozor/polymarket-kalshi-arbitrage-vs-sportsbooks-30-days-of-esports-data-1dc2)
- Peso por juego: League of Legends es ~18% de los arbs con Polymarket y ~26% de los de Kalshi, frente a ~7% en el conjunto de casas. Dota 2 es ~20% de los arbs con Polymarket. CS2 es el título más grande en conjunto. — [DEV/Dozor](https://dev.to/dozor/polymarket-kalshi-arbitrage-vs-sportsbooks-30-days-of-esports-data-1dc2)
- Arbs con Polymarket o Kalshi: 15,9% del total (Polymarket 13,2%, Kalshi 2,8%). Reparto live/prematch: en todas las casas ~81% live / 19% prematch; en Polymarket 64% / 36%; en Kalshi 65% / 35%. — [DEV/Dozor](https://dev.to/dozor/polymarket-kalshi-arbitrage-vs-sportsbooks-30-days-of-esports-data-1dc2)
- Snapshot de OddsPapi (1-oct-2026, 75 partidos de 7 deportes: Nations League, Euroliga, WTA Beijing, MLB, NFL/NCAAF, NHL, UFC). Polymarket cotizaba 70 de los 75. De 2.938 pares, 42 arbs sobrevivieron al filtrado, con edge mediano de 0,44% y máximo de 1,53% (Hapoel en Polymarket 2,439 + Real Madrid en FanDuel 1,74, Euroliga). Los "arbs" de 29% y 8% eran outliers: una orden de $1,15 y una cuota stale. La profundidad mediana del mejor nivel en Polymarket era de $4.023. Es blog de un vendedor y las fechas del post son inconsistentes. — [OddsPapi blog: Polymarket arbitrage vs local bookmakers](https://oddspapi.io/blog/polymarket-arbitrage-local-bookmakers/)
- En ese mismo snapshot, el overround mediano de Polymarket en ganador era ~1,0% (baloncesto, tenis, NFL, NHL, MMA) y 0,98% en over/under de fútbol. En Hapoel–Real Madrid: Pinnacle 3,86%, bet365 7,12%, FanDuel 5,55%, Kalshi 1,01%. — [OddsPapi blog](https://oddspapi.io/blog/polymarket-arbitrage-local-bookmakers/)

**Calibración y sesgos (evidencia académica)**
- Cardozo y Rivero-Wildemauwe (arXiv 2609.12878, sep-2026; datos de nov-2022 a mar-2026) encuentran que el sesgo favorito-longshot de doble cara es robusto en Crypto y Politics pero "sorprendentemente ausente en Sports". En deportes, los longshots dan +2,43% equiponderado (IC −0,93 a 5,79) y los favoritos −0,23% (IC −0,285 a −0,174). Sports suma 94,6M compras, $8,72B pagados (36,8% del total) y 309.782 mercados hijos resueltos. Deportes se queda más cerca de la diagonal de calibración que otras categorías. No hay desglose por liga. — [arXiv 2609.12878](https://arxiv.org/html/2609.12878v1)
- Moshrefi (Princeton, arXiv 2607.14430, jul-2026) estudió ~23M trades moneyline en Kalshi (NBA, MLB, NHL; mar–may 2026), no Polymarket. La calibración es casi perfecta entre ~30 y 240 min antes del cierre y se degrada mucho en los últimos 10 min. En ese tramo la pendiente Platt es 1,62 en NBA, 2,05 en MLB y 4,56 en NHL; la distorsión es mayor en NHL > MLB > NBA. Los parlays están sobrevalorados ~3% por pierna adicional (ratio mediano 1,22 con 10 piernas y 1,31 con 11). — [arXiv 2607.14430](https://arxiv.org/html/2607.14430)
- El paper "Polymarket-v1 Database" describe un patrón transversal coherente con el sesgo favorito-longshot: tokens a ≤0,30 dan retornos negativos y tokens a ≥0,40 retornos positivos. No separa deportes. — [arXiv 2606.04217](https://arxiv.org/html/2606.04217v2)

**Liquidez**
- NBA fue cerca del 30% de la actividad deportiva de Polymarket en 2025. — [arXiv 2605.00864 (Cheng, Yang, Zou)](https://arxiv.org/abs/2605.00864)
- Según una guía de market making, en los minutos finales la liquidez "se evapora" porque los makers se retiran para evitar el riesgo de resolución. — [startpolymarket: market making](https://startpolymarket.com/strategies/market-making/)
- La página de tenis de Polymarket US lista partidos de nivel Challenger (p. ej. Challenger Tiburon), pero que exista el listado no dice nada de su profundidad. — [polymarket.us/sports/tennis](https://polymarket.us/sports/tennis)

### Inferences
- El patrón del usuario (Polymarket vs SX Bet casi sin arbs; Polymarket vs 1xBet entre 0,5 y 4%) cuadra con la evidencia externa. Polymarket, Kalshi, SX Bet y Pinnacle se mueven juntos porque los MM anclan a Pinnacle. Las brechas aparecen cuando una casa blanda o regional (1xBet, Olimp, Marathon, BetBoom) tarda en mover su línea o pone una línea propia en mercados con poca cobertura sharp. El ROI mediano de 2,96% del par 1xBet–Polymarket en eSports coincide con el rango que observa el usuario.
- Es probable que los segmentos de nicho que ve el usuario (ITF, eSports menores, ligas bajas) den más brechas por dos razones: tienen menos MM anclados a Pinnacle (que a veces ni cotiza esos mercados) y tienen menos profundidad. Esa misma falta de profundidad limita el tamaño ejecutable (ver la mediana de $4.023 en el mejor nivel en mercados grandes; en nicho debería ser mucho menor).
- Las comisiones taker de Polymarket deportes (sección 3) se comen buena parte de un arb del 0,5–1,5% cuando el precio está cerca de 50¢. Por eso los arbs "netos" relevantes van a estar sobre todo en el extremo alto del rango o con la pata de Polymarket ejecutada como maker.

### Gaps
- No encontré ningún estudio ni dashboard (Dune u otro) que mida la divergencia Polymarket vs Pinnacle o vs casas blandas desglosada por liga, deporte o tipo de mercado (handicap, totales, props), ni específicamente para tenis ITF o fútbol de ligas menores.
- No encontré datos cuantitativos sobre cómo cambia la brecha según las horas que faltan para el inicio (p. ej. 24 h vs 1 h antes). Solo hay el reparto live/prematch de Dozor y la calibración por minutos al cierre en Kalshi (Moshrefi).
- No hay datos fiables sobre qué porcentaje de la liquidez deportiva de Polymarket aportan MM internos o externos. Solo una mención a que Polymarket habría incorporado MM internos ([Sacra](https://sacra.com/c/polymarket/)), sin cifras.

---

## 2. ¿Qué tan rápido se cierran las brechas? Papers, blogs, dashboards y proyectos GitHub que lo miden (incluido Kalshi–Polymarket)

### Takeaway
Las brechas se cierran en segundos. Mediana de ~2–4 s en live, ~10 s en prematch con pata en exchange, ~9 s entre Kalshi y Polymarket, ~3,6 s para los arbs intra-mercado en NBA. Aun así, una cola relevante dura más de 60 s, sobre todo en prematch (17% de los arbs prematch con exchange; 8,3% de los arbs con Polymarket en general). Polymarket además aplica un retraso de 1–3 s a las órdenes marketable en deportes y vacía el libro al inicio del partido, así que el live es casi inejecutable para un escáner por polling.

### Cited Findings
**Tiempos de vida medidos**
- Dozor (eSports, sep–oct 2026, resolución de ~2 s). Live, solo casas: mediana 2,0 s, P90 14,2 s, 1,2% ≥60 s, ROI mediano 3,37%. Live con pata exchange: mediana 2,0 s, P90 24,4 s, 2,9% ≥60 s, ROI 3,09%. Prematch, solo casas: mediana 14,1 s, P90 93,5 s, 17,8% ≥60 s, ROI 1,54%. **Prematch con exchange: mediana 10,3 s, P90 177,4 s, 17,3% ≥60 s, ROI mediano 2,30%.** Vida mediana de los arbs con Polymarket 4,1 s y con Kalshi 4,0 s. Duraron ≥60 s el 8,3% de los de Polymarket, el 6,9% de los de Kalshi y el 4,5% del total. Duraron ≥5 s el 44,6% de los arbs con exchange frente al 29,7% de los de solo casas. Los propios autores advierten que un escáner más lento registra menos arbs cortos. — [DEV/Dozor](https://dev.to/dozor/polymarket-kalshi-arbitrage-vs-sportsbooks-30-days-of-esports-data-1dc2)
- Kalshi–Polymarket en deportes: brechas abiertas una mediana de ~9 s, el 96% cerradas en menos de 30 s. En un partido, cada oscilación (incluida la de un VAR) se cerró en ~45 s. — [DEV michaelmustopo](https://dev.to/michaelmustopo/built-a-prediction-market-arbitrage-no-sizable-arbitrage-found-36cf)
- El README de sportstrades dice que las brechas in-game duran "unos seis segundos". — [GitHub VV1Git/sportstrades](https://github.com/VV1Git/sportstrades)
- Cheng, Yang y Zou (arXiv 2605.00864, abr-2026) analizaron 75M+ snapshots del libro de órdenes de 173 partidos NBA en Polymarket. Solo hubo 7 episodios ejecutables de arbitraje en un solo mercado (in-game), con duración mediana de 3,6 s. El arbitraje combinatorio fue más frecuente (290 episodios), sobre todo en los minutos finales del live, con retorno mediano de 101 pb. En el 76,9% de los casos el tamaño ejecutable medio era de solo 14,8 shares. El resumen no aclara si el retorno es neto de comisiones. — [arXiv 2605.00864](https://arxiv.org/abs/2605.00864)

**Papers sobre arbitraje y descubrimiento de precio entre venues (no solo deportes)**
- Saguillo, Ghafouri, Kiffer y Suarez-Tangil (IMDEA; arXiv 2508.03474; AFT 2025) estiman unos $40M de beneficio realizado por arbitrajistas en Polymarket. Distinguen arbitraje de rebalanceo intra-mercado y combinatorio inter-mercado, usan datos on-chain y LLMs para detectar dependencias. No es específico de deportes (dato de 2025). — [arXiv 2508.03474](https://arxiv.org/pdf/2508.03474)
- Ng, Peng, Tao y Zhou, "Price Discovery and Trading in Modern Prediction Markets" (SSRN 5331995; jul-2025, revisado abr-2026), sobre las elecciones de EE. UU. 2024. Polymarket lidera a Kalshi en el descubrimiento de precio, sobre todo con alta liquidez. Las brechas entre plataformas persisten e implican oportunidades de arbitraje económicamente relevantes. Solo vi el abstract. — [SSRN 5331995](https://papers.ssrn.com/abstract=5331995)
- Aktuğ y Torul (abr-2026), nueve plataformas en las elecciones 2024. Polymarket y Betfair suman ~85% de la information share de Hasbrouck. Polymarket lidera a frecuencia de 5 min y Betfair a 1 h. — [Boğaziçi PDF](https://web.bogazici.edu.tr/torul/pridis.pdf)
- Resumen de Quantpedia de un estudio de arbitraje Polymarket/Kalshi/PredictIt: Polymarket suele liderar a Kalshi por mayor liquidez, sobre todo en las últimas horas antes del cierre. No pude identificar a los autores. — [Quantpedia](https://quantpedia.com/systematic-edges-in-prediction-markets/)

**Microestructura de Polymarket deportes que afecta la ejecución**
- Las fuentes oficiales difieren sobre el retraso. El centro de ayuda habla de un retraso de 3 s en la colocación de órdenes marketable en mercados deportivos, y de un retraso taker de 1 s en prueba en NBA y MLB. Los docs del ciclo de vida de órdenes hablan de 1 s. — [Polymarket docs: order lifecycle](https://docs.polymarket.com/concepts/order-lifecycle); [Polymarket Help: limit orders](https://help.polymarket.com/en/articles/13364444-limit-orders)
- El retraso se expone por mercado como `secondsDelay` (en la API de Gamma y en `market.trading.secondsDelay` en los SDK). La hora de inicio se expone como `gameStartTime`. — [Polymarket docs: market details](https://docs.polymarket.com/market-data/market-details)
- Las órdenes limit que quedan abiertas se cancelan automáticamente al inicio oficial del partido. Si el partido empieza antes de lo previsto, puede que no se cancelen. — [Polymarket Help: limit orders](https://help.polymarket.com/en/articles/13364444-limit-orders)

**Herramientas y proyectos GitHub (Kalshi–Polymarket–casas)**
- VV1Git/sportstrades: escáner paper-trading de Kalshi vs Polymarket vs moneylines de casas (DraftKings vía ESPN, u otras con The Odds API). Recorre los dos libros para dimensionar. Asume comisión de Polymarket deportes 0,05 × shares × p × (1−p) y de Kalshi 0,07 × C × p × (1−p) (multiplicador 0,5 en MLB). Usa margen neto mínimo de 0,5%. — [GitHub VV1Git/sportstrades](https://github.com/VV1Git/sportstrades)
- ArbiBot (sports y eSports en Kalshi y Polymarket), realfishsam/prediction-market-arbitrage-bot (matching difuso; el ejemplo es un dry run) y Kalshi-Polymarket-Ai-bot. Ninguno publica P&L real auditado. — [ArbiBot README](https://cdn.jsdelivr.net/gh/axwelbrand-byte/arbibot@master/README.md); [realfishsam](https://github.com/realfishsam/prediction-market-arbitrage-bot)
- Dozor: escáner de surebets de eSports con 25 casas más Polymarket y Kalshi, con plan gratis. — [DEV/Dozor](https://dev.to/dozor/polymarket-kalshi-arbitrage-vs-sportsbooks-30-days-of-esports-data-1dc2)
- PredictionMarketsPicks marca un ARB solo si el par neto deja ≥2¢ por contrato tras los costes de ambos venues. — [predictionmarketspicks arb-scanner](https://predictionmarketspicks.com/tools/arb-scanner)
- Datasets para medir por cuenta propia: Jon-Becker/prediction-market-analysis (se presenta como el mayor dataset público de trades de Polymarket y Kalshi) y honest-odds (calibración de 2.946 mercados de Polymarket). — [GitHub jon-becker](https://github.com/jon-becker/prediction-market-analysis); [GitHub honest-odds](https://github.com/CH4RL3I/honest-odds)

### Inferences
- Con un escáner por polling de 30–60 s solo se capturan los arbs de la cola larga, es decir el ~17% de los arbs prematch que duran ≥60 s. Eso favorece el prematch, que es justo donde Polymarket tiene más peso relativo (36% de sus arbs frente al 19% del resto).
- En live, el retraso de 1–3 s de Polymarket deportes más una vida mediana de ~2–4 s implica que la pata de Polymarket casi nunca se ejecuta al precio visto. El paper-trading del usuario debería descartar o penalizar los arbs live con pata Polymarket.
- Al decidir con clv.py, conviene registrar el first-seen/last-seen de cada arb (como hace Dozor) para estimar qué fracción sería ejecutable con la latencia real del usuario.

### Gaps
- No encontré dashboards de Dune ni análisis públicos que midan específicamente la vida de las brechas Polymarket vs 1xBet en fútbol o tenis, solo en eSports (Dozor).
- No pude leer el texto completo de Ng et al. (magnitudes de las desviaciones Kalshi–Polymarket) ni de Reichenbach y Walther (2026), "When Can Prediction Market Prices Be Trusted?".
- No está claro qué ligas tienen hoy un `secondsDelay` de 1 s frente a 3 s. Hay que leerlo mercado por mercado.

---

## 3. Comisiones de Polymarket en deportes, liquidez y cómo cotizan los market makers

### Takeaway
Polymarket cobra comisión taker en deportes con la fórmula fee = C × rate × p × (1−p). Los makers pagan 0 y reciben rebate. La tasa de deportes ha subido durante 2026: ~0,44% de pico efectivo en febrero, 0,75% (rate 0,03) desde el 30-mar, y la documentación oficial actual muestra **rate 0,05 (pico de $1,25 por 100 shares) con 15% de rebate a makers**. Hay que leer `feeSchedule` en cada mercado, porque varía según la fecha de creación y la categoría.

### Cited Findings
- Docs oficiales actuales: Sports, taker fee rate 0,05, maker rebate 15%. Fórmula fee = C × feeRate × p × (1−p). Pico de $1,25 por 100 shares a 50¢. Los makers nunca pagan. La comisión se aplica en el match. Geopolitics no tiene comisión. Crypto 0,07, Finance y Politics 0,04, Economics/Culture/Weather/Other 0,05. — [Polymarket docs: fees](https://docs.polymarket.com/trading/fees)
- Por mercado, la API de Gamma expone `feesEnabled` y `feeSchedule {rate, exponent, takerOnly, rebateRate}`. Ejemplo: `https://gamma-api.polymarket.com/markets/slug/<slug>`. — [Polymarket docs: market details](https://docs.polymarket.com/market-data/market-details)
- Cronología según Pine Analytics (25-mar-2026). Comisiones de crypto desde el 5-ene-2026. **Comisiones de deportes desde el 18-feb-2026.** Casi todas las demás categorías desde el 30-mar-2026. El pico efectivo de deportes pasa de 0,44% a 0,75%. Fórmula con exponente: fee = C × p × feeRate × (p(1−p))^exponent, exponente 1 en deportes. El volumen deportivo subió de $100–150M/día a $150–250M/día tras introducir las comisiones. Comisión media ponderada ~0,76% del volumen taker. — [Pine Analytics: Polymarket Fee Rollout](https://pineanalytics.substack.com/p/polymarket-fee-rollout)
- iGB (27-mar-2026): desde el 30-mar las comisiones de deportes "casi se duplican" (pico efectivo de 0,75% a 50%) y aplican solo a mercados creados desde esa fecha. El rebate para makers de deportes sería del 25%. — [iGaming Business](https://igamingbusiness.com/prediction-markets/polymarket-sports-fee-hike-2026/)
- **Contradicción:** River Markets (actualizado el 9-ago-2026) todavía dice 3% en deportes (V2, desde el 30-mar), $0,75 por 100 contratos a 50¢, con rebates financiados con el 20% de las comisiones. Los docs oficiales actuales dicen 0,05 y 15%. No encontré la fecha exacta del paso de 0,03 a 0,05. El repo sportstrades (sep-2026) ya usa 0,05. — [River Markets](https://www.rivermarkets.com/insights/polymarket-fees.html); [Polymarket docs](https://docs.polymarket.com/trading/fees); [sportstrades](https://github.com/VV1Git/sportstrades)
- Polymarket US es otro exchange con su propio calendario de comisiones (efectivo desde el 1-jul-2026). Hay fuentes que dan 5% de taker en sports en US. Otra compara 100 contratos a 50¢: $1,25 en Polymarket US frente a $1,75 en Kalshi. — [River Markets: Polymarket US fees](https://www.rivermarkets.com/insights/polymarket-us-fees.html); [predictreport.io](https://predictreport.io/blog/best-prediction-market-apis-builders)
- La comisión taker de Kalshi que usa sportstrades es 0,07 × C × p × (1−p), redondeada al centavo, con multiplicador 0,5 en partidos MLB. — [GitHub VV1Git/sportstrades](https://github.com/VV1Git/sportstrades)
- Hay jugadores sharp (Canzhi Ye, Isaac Rose-Berman) que advierten que subir comisiones podría expulsar a los sharps y reducir la liquidez. — [iGaming Business](https://igamingbusiness.com/prediction-markets/polymarket-sports-fee-hike-2026/)
- Los MM deportivos anclan a Pinnacle para el pregame, ponen poca liquidez cerca del precio justo y mucha más lejos, y bajan precios o se retiran ante noticias o al acercarse el inicio (testimonio de oct-2025). — [Polymarket "The Oracle"](https://news.polymarket.com/p/meet-your-market-maker)

### Inferences
- Comisión como % del stake de la pata Polymarket = rate × (1−p). Con rate 0,05: a p=0,5 es 2,5%, a p=0,3 (underdog) 3,5%, a p=0,8 1,0%, a p=0,9 0,5%. Sobre el total invertido en un arb de dos vías, el arrastre es ≈ rate × p × (1−p), con un máximo de 1,25% a 50¢ (0,75% si el mercado tiene rate 0,03). Es cálculo propio a partir de la fórmula oficial.
- Una aproximación práctica para el escáner: cuota decimal efectiva de Polymarket como taker ≈ (1/p) × (1 − rate × (1−p)). Un arb bruto del 1% con la pata de Polymarket cerca de 50¢ queda en negativo con rate 0,05. Solo los arbs brutos de más de ~1,5–2,5% (según p) sobreviven como taker. Los arbs del 0,5–1% solo tienen sentido si la pata de Polymarket entra como maker (comisión 0 + rebate), aceptando riesgo de no ejecución.
- Como los mercados creados antes del 30-mar (o de otras categorías) pueden tener otro `feeSchedule` o `feesEnabled=false`, el escáner debería leer `feeSchedule.rate` y `exponent` de Gamma en cada mercado en vez de usar una constante.

### Gaps
- No encontré la fecha ni el anuncio oficial del cambio de rate de deportes de 0,03 a 0,05, ni del cambio de rebate de 25% a 15%.
- No encontré datos sobre spreads o profundidad del libro por liga antes y después de las comisiones.

---

## 4. ¿Qué APIs gratuitas dan la mejor cobertura por request para encontrar arbitraje Polymarket-vs-casa, y cuáles son sus límites exactos?

### Takeaway
Para un escáner pequeño, **The Odds API** es la que más rinde por crédito. Una llamada /odds con hasta 10 casas cuesta 1 crédito por mercado, e incluye Pinnacle, 1xBet, Polymarket, Kalshi, Betfair Exchange, Matchbook y Smarkets. Su cobertura de nicho (ITF, ligas bajas) es limitada y no cubre SX Bet, Stake ni Cloudbet. **OddsPapi** tiene la cobertura más amplia (350+ casas, incluidas Pinnacle, 1xBet, Stake, Cloudbet, SX Bet, Polymarket y Kalshi) pero solo 250 requests al mes. Las APIs directas de Polymarket, SX Bet y Kalshi son gratis. El resto (OpticOdds, OddsJam, Unabated, SportsDataIO, BetsAPI) no tiene una capa gratuita útil para esto.

### Cited Findings

**Tabla comparativa (datos de vendedores salvo donde se indica; verificados en oct-2026 cuando fue posible)**

| API | Capa gratis (límite exacto) | Pinnacle | Polymarket | Kalshi | SX Bet | 1xBet | Stake | Cloudbet | Betfair Ex | Live | Latencia / refresco | Plan de pago más barato |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **The Odds API** | 500 créditos/mes; /odds cuesta mercados × regiones; cada 10 casas en `bookmakers` = 1 región; /sports y /events gratis; histórico ×10 | Sí (`pinnacle`, eu; scrapeado de su web pública, "puede ir con retraso") | Sí (`polymarket`, región us_ex) | Sí (`kalshi`, us_ex) | No listado | Sí (`onexbet`, eu) | No listado | No listado | Sí (uk/eu/au) + Matchbook, Smarkets | Sí | Snapshots históricos cada 5 min; intervalo live no confirmado | $30/mes (20K créditos); $59 (100K); $119 (5M); $249 (15M) |
| **OddsPapi** | 250 requests/mes (la home llegó a decir 200: contradicción); histórico gratis (máx. 3 casas por llamada) | Sí (también Singbet, SBOBet) | Sí (`polymarket` y `polymarket.us` por separado, con profundidad en USD por nivel) | Sí | Sí (`sx.bet`) | Sí | Sí | Sí | Sí (`betfair-ex`) + Matchbook, ProphetX, 4casters | Sí | No publicada; WebSocket "disponible" en free según un post de jun-2026, "solo de pago" según otro de mar-2026 | No publicado (B2C/B2B a medida) |
| **Odds-API.io** | **Ya no da claves gratis nuevas** ("not issuing new free keys"); las existentes: 100 req/h (500/día), 2 casas recreativas | Solo de pago (dicen haber sustituido Pinnacle por Betfair Ex/SingBet/Polymarket) | De pago (no confirmado) | No confirmado | No confirmado | No confirmado | No confirmado | No confirmado | De pago | Sí | WebSocket solo en prueba de 3 días | Solo $65/mes (2 casas, 5.000 req/h); Pro $299 (15 casas) |
| **SportsGameOdds** | Amateur: 2.500 objetos/mes (1 objeto = 1 evento con todas sus casas y mercados), 10 req/min, refresco cada 10 min, 8 ligas, 9 casas | Solo Pro ($299) | Sí (Kalshi, Polymarket, Novig en su "prediction market API"; plan no especificado) | Sí | No mencionado | Desde Rookie ($99) | No mencionado | No mencionado | No mencionado | Sí | 10 min (free) / 3 min (Rookie) / sub-minuto (Pro); la FAQ dice 30–60 s para prediction markets | $99/mes (100K objetos) |
| **OpticOdds** | Ninguna | Sí (sharps soportadas) | No confirmado | No confirmado | — | — | — | — | — | Sí | — | Solo por cotización; estimación comunitaria ~$5.000/mes por deporte (no verificado) |
| **SportsDataIO** | Prueba gratis con datos **scrambled** (no reales) | — | — | — | — | — | — | — | — | Live con acuerdo comercial | — | Odds ~$99/mes o $599/año (dato de terceros) |
| **API-Football** | No verificado (web 403); un competidor dice que las odds estándar son prematch con refresco ~1 vez al día | No enfocado en sharps (según competidor) | No | No | No | No verificado | — | — | — | No verificado | — | No verificado |
| **Betfair Exchange (Delayed App Key)** | Gratis; snapshots retrasados 1–180 s; solo 3 niveles de precio; sin `totalMatched`; requiere cuenta | — | — | — | — | — | — | — | Sí (exchange real, en producción) | Sí (retrasado) | 1–180 s de retraso | Live key: £299 pago único (cuenta financiada y verificada) |
| **OddsJam API** | Ninguna | Sí | — | — | — | — | — | — | — | Sí | — | Solo por cotización; estimaciones de terceros desde ~$499/mes |
| **Unabated API** | $0 solo para creadores de contenido con tráfico de deeplinks, con 30 s de retraso | Sí | — | — | — | — | — | — | — | Sí | 30 s de retraso en free | Concierge $799/mes; Advanced $2.999/mes |
| **BetsAPI** | Sin capa gratis; prueba de 1 día por 2,00 | Sí (paquetes por casa) | — | — | — | — | — | — | — | Sí | — | "Desde $10/mes"; Bet365 API 150/mes; Everything API 300/mes |
| **Oddpool** (prediction markets) | 1.000 requests/mes, 1 req/s | — | Venues no confirmados en docs | — | — | — | — | — | — | — | — | Pro/Premium (precio no visto) |
| **Matcher Kalshi–Polymarket (michaelmustopo)** | API REST gratis, 60 req/min, más servidor MCP | — | Sí | Sí | — | — | — | — | — | Sí | — | — |
| **PinnWire** (revendedor de Pinnacle) | Clave demo: 10 req/min y 50/día | Sí | — | — | — | — | — | — | — | — | — | — |
| **APIs directas: Polymarket** | Gratis; Gamma /markets 300 req/10 s, /events 500/10 s; CLOB /book 1.500/10 s, /books 500/10 s, /prices-history 1.000/10 s; límite por IP, se encola en vez de rechazar | — | Sí | — | — | — | — | — | — | Sí (WebSocket) | Tiempo real | Gratis |
| **APIs directas: SX Bet** | Lectura REST pública y gratis (api.sx.bet; WS en realtime.sx.bet); límites no confirmados; odds en probabilidad escalada ×10^20 | — | — | — | Sí | — | — | — | — | Sí | Tiempo real | Gratis |
| **APIs directas: Kalshi** | Lectura gratis; ~10 req/s (dato de terceros) | — | — | Sí | — | — | — | — | — | Sí | Tiempo real | Gratis |
| **Pinnacle oficial** | **Cerrada al público desde el 23-jul-2025**; acceso caso a caso (api@pinnacle.com) | Sí | — | — | — | — | — | — | — | — | — | Acuerdo comercial |

Fuentes de la tabla:
- The Odds API, precios y planes. — [the-odds-api.com](https://the-odds-api.com/)
- The Odds API, coste en créditos (markets × regions; 10 casas = 1 región; /sports y /events gratis; histórico 10×; snapshots cada 5 min desde sep-2022). — [The Odds API v4 docs](https://the-odds-api.com/liveapi/guides/v4/)
- The Odds API, casas y regiones (us_ex: betopenly, kalshi, novig, polymarket, prophetx; pinnacle eu "from public website… may be delayed"; onexbet eu; betfair_ex_uk/eu/au; matchbook; smarkets; marathonbet; SX Bet, Stake y Cloudbet no listados). — [The Odds API bookmakers](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)
- OddsPapi, 250 req/mes, 350+ casas, Pinnacle/Singbet/SBOBet, histórico sin multiplicador, sin precio de pago publicado (post de mar-2026). — [OddsPapi pricing comparison](https://oddspapi.io/blog/odds-api-pricing-2026-comparison/)
- OddsPapi, 250 req/mes, WebSocket "disponible" en free (post del 12-jun-2026). — [OddsPapi vs The Odds API free tier](https://oddspapi.io/blog/?p=2967)
- OddsPapi, slugs `pinnacle`, `betfair-ex`, `matchbook`, `sx.bet`, `prophetx`, `4casters`, `polymarket`, `limitless-ex`, `myriad`, `kalshi`; /v4/historical-odds gratis con máx. 3 casas por llamada (may-2026). — [OddsPapi prediction market API 2026](https://oddspapi.io/blog/prediction-market-api-2026-stack/)
- OddsPapi, polymarket y polymarket.us como feeds separados; profundidad en USD por nivel; free 250 req/mes. — [OddsPapi Polymarket arbitrage](https://oddspapi.io/blog/polymarket-arbitrage-local-bookmakers/)
- OddsPapi, Cloudbet con live y WebSocket; free 250 req/mes. Una página de terceros dice "5.000 requests/mes" para una ruta Cloudbet: contradicción, no verificado. — [OddsPapi Cloudbet](https://oddspapi.io/sportsbooks/cloudbet)
- OddsPapi, 1xBet, Stake y BC.Game entre las casas cripto; Betfair Ex, Polymarket y Kalshi entre exchanges y prediction markets. — [OddsPapi BetsAPI alternative](https://oddspapi.io/blog/betsapi-alternative/)
- La home de OddsPapi decía 200 req/mes. — [oddspapi.io/en](https://oddspapi.io/en)
- Odds-API.io, planes y precios. — [odds-api.io/pricing](https://odds-api.io/pricing)
- Odds-API.io, claves gratis pausadas, 2 casas recreativas, 100 req/h, 500/día, sharps/exchanges/PM solo de pago. — [odds-api.io/pricing/free](https://odds-api.io/pricing/free)
- Odds-API.io, sustitución de Pinnacle por Betfair Ex/SingBet/Polymarket. — [odds-api.io blog](https://odds-api.io/blog/pinnacle-api-shutdown-alternatives)
- SportsGameOdds, planes, conteo por objeto, Pinnacle solo Pro, 1xBet desde Rookie. — [sportsgameodds.com/pricing](https://sportsgameodds.com/pricing)
- SportsGameOdds, Kalshi/Polymarket/Novig con refresco de 30–60 s. — [SportsGameOdds prediction market API](https://sportsgameodds.com/prediction-market-api)
- OpticOdds: sin capa gratis, precio por cotización. — [odds-api.io compare](https://odds-api.io/compare/optic-odds); [apibetting.com](https://www.apibetting.com/providers/opticodds)
- SportsDataIO: datos scrambled en la prueba. — [sportsdata.io getting started](https://sportsdata.io/developers/getting-started)
- SportsDataIO: precios. — [sportsapis.dev review](https://sportsapis.dev/apis/sportsdata-io)
- API-Football (afirmación de un competidor). — [OddsPapi API-Football alternative](https://oddspapi.io/blog/?p=3128)
- Betfair Delayed Key: 1–180 s, 3 niveles, sin totalMatched, £299 por la live key. — [Betfair developer docs](https://betfair-developer-docs.atlassian.net/wiki/x/fyUp)
- OddsJam API: por cotización. — [oddspapi OddsJam](https://oddspapi.io/blog/oddsjam-api-alternative/); [sportsapi.com](https://sportsapi.com/api-directory/oddsjam/)
- Unabated: $799 / $2.999, free con 30 s de retraso para creadores. — [Unabated concierge](https://unabated.com/odds-api/concierge); [Unabated free](https://unabated.com/odds-api/free)
- BetsAPI. — [BetsAPI pricing](https://betsapi.com/mm/pricing_table); [BetsAPI docs](https://betsapi.com/docs/)
- Oddpool. — [Oddpool rate limits](https://docs.oddpool.com/rate-limits.md)
- Matcher Kalshi–Polymarket gratis. — [DEV michaelmustopo](https://dev.to/michaelmustopo/built-a-prediction-market-arbitrage-no-sizable-arbitrage-found-36cf)
- PinnWire y el cierre de Pinnacle. — [sportsapis.dev Pinnacle API](https://sportsapis.dev/pinnacle-api); [DEV: Pinnacle killed its public API](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)
- Polymarket, rate limits. — [Polymarket docs: rate limits](https://docs.polymarket.com/api-reference/rate-limits)
- SX Bet, lectura pública (resumen de terceros). — [skills.sh SX Bet](https://skills.sh/site/docs.sx.bet/sx-bet); [OddsPapi SX Bet](https://oddspapi.io/blog/?p=2836)
- Kalshi, ~10 req/s y lectura gratis (dato de vendedor). — [OddsPapi Polymarket/Kalshi API guide](https://oddspapi.io/blog/polymarket-api-kalshi-api-vs-sportsbooks-the-developers-guide/)

**Otros datos útiles**
- Apify "Polymarket & Kalshi odds": desde el 10-oct-2026 cobra $2 por cada 1.000 mercados. — [Apify](https://apify.com/sports-odds-lab/polymarket-kalshi-odds)
- Prediction Hunt: capa gratis sin tarjeta para Kalshi, Polymarket, PredictIt, ProphetX y Opinion (vendedor). — [Prediction Hunt](https://www.predictionhunt.com/blog/best-api-for-prediction-markets)
- The Odds API: con 0 créditos devuelve 401/429 hasta el reset mensual, sin overage. Ejemplo: 500 créditos ≈ 83 llamadas con 3 mercados × 2 regiones. Lo dice OddsPapi, un competidor. — [OddsPapi blog](https://oddspapi.io/blog/?p=2967)

### Inferences
- **Mejor cobertura por request con lo que el usuario ya tiene.** En The Odds API, una llamada /odds con `bookmakers=pinnacle,onexbet,polymarket,kalshi,betfair_ex_eu,matchbook,marathonbet,...` (≤10 casas) y `markets=h2h` cuesta 1 crédito por liga. Con 500 créditos/mes se pueden hacer ~500 barridos liga-mercado al mes, unos 16 al día. Alcanza para una referencia sharp (Pinnacle/Betfair) en ligas mayores, no para polling continuo. /events y /sports son gratis y sirven para el matching. Es cálculo propio a partir de las reglas de coste documentadas.
- OddsPapi (250 req/mes) conviene reservarlo para lo que The Odds API no cubre: SX Bet, Stake, Cloudbet, Singbet, y probablemente las ligas de nicho. También para histórico gratis (CLV, máx. 3 casas por llamada).
- Como los arbs con Polymarket duran segundos o minutos, ninguna capa gratis de agregador permite detectarlos en tiempo real. La arquitectura más barata es: Polymarket CLOB/WebSocket + SX Bet + feed de 1xBet en directo, gratis y en tiempo real, y usar The Odds API/OddsPapi solo como referencia sharp (Pinnacle) para validar el CLV y filtrar las líneas stale de 1xBet.
- SportsGameOdds Amateur (refresco de 10 min, 8 ligas) y Odds-API.io (sin claves nuevas, sin sharps ni PM en free) no aportan valor a este caso de uso.

### Gaps
- No pude verificar los límites y la cobertura de odds de API-Football (su web devolvió 403), ni los intervalos oficiales de refresco live/prematch de The Odds API, ni la latencia medida de OddsPapi.
- No encontré si The Odds API cubre torneos ITF de tenis o ligas de fútbol de tercera o cuarta división. Hay que comprobarlo con /sports (gratis).
- No está confirmado si la capa gratis de OddsPapi incluye WebSocket (las fuentes del propio vendedor se contradicen) ni cuál es su primer plan de pago.
- No confirmé si Cloudbet tiene una API oficial gratuita (las fuentes se contradicen) ni los límites oficiales de la API pública de SX Bet.
- Para Betfair Exchange no verifiqué si hay restricciones geográficas que afecten al usuario (Colombia).
