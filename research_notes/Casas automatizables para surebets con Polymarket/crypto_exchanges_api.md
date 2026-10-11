# Crypto / on-chain sports exchanges and sportsbooks with legitimate programmatic betting (candidates to pair with Polymarket), status as of Oct 2026

Scope note: the research ran on 2026-10-10. Many sources are official docs, which carry no dates. Older items are flagged inline. Where a ToS is quoted, the quote is verbatim from a fetch of the live page on that date. None of this is legal advice, and nothing here describes ways around geoblocks or KYC.

---

## 1. Which venues offer an official API, SDK or smart-contract interface to PLACE bets/orders programmatically, and do they have rules against bots?

### Takeaway
**SX Bet** (V3 REST order API + EIP-712) and **Limitless** (self-serve HMAC API key + EIP-712 orders, with official Python/TS/Go/Rust SDKs) are the cleanest "official API for bots" venues. **Azuro** is designed for programmatic access: bets are EIP-712-signed orders sent to its public backend, then relayed on-chain, with an official SDK. But the main frontend (bookmaker.xyz) bans "bots". **Overtime**'s trade contracts are on-chain, but its data API (which supplies the odds and merkle proofs needed to trade) is gated, and its docs say outright that it is not for "arbitrage or any sorts of automated trading projects".

### Cited Findings

**SX Bet (V3, current API generation)**
- V3 base URLs: mainnet REST `https://api.sx.bet`, testnet `https://api.toronto.sx.bet`. Realtime is Centrifugo at `wss://realtime.sx.bet/connection/websocket`, not Ably as in older versions. — [SX Bet docs (V2→V3 migration)](https://docs.sx.bet/)
- Authenticated requests need an `x-sx-api-key` header. V2 keys do not work in V3, so a new key must be generated under Account → Overview. Orders are signed with EIP-712 using the EOA private key, and the maker is the EOA, never the proxy. — [SX Bet docs](https://docs.sx.bet/)
- V3 requires deploying and funding a proxy wallet (`POST /user/deploy-proxy`, `POST /user/transfer-to-proxy`) before trading. Bets are placed, collateralized and paid out through that proxy contract. — [SX Bet docs](https://docs.sx.bet/); [New in V3](https://docs.sx.bet/developers/new-in-v3)
- A single signed endpoint, `POST /orders-v3`, covers all orders, and the old fill endpoint is gone. `timeInForce`: GTC rests as a maker order, while IOC/FOK execute immediately as taker orders. Expiry is mandatory. Orders are async by default, and `waitForOutcome: true` makes them synchronous. A dead-man heartbeat (`POST /heartbeat/v3`, 0–3600 s) auto-cancels open orders. — [SX Bet docs](https://docs.sx.bet/)
- V3 claims about 50% lower order-placement latency than V2 and 2–3× faster bet-to-onchain settlement. It also claims to remove V2 fill failures and cancel/fill races, and adds "automatic order bashing", where crossing orders fill immediately. — [New in V3](https://docs.sx.bet/developers/new-in-v3)
- ToS bot clauses (help-center T&C, "updated over a week ago" as of the fetch): "You are not allowed to use any kind of robots and programmed devices to participate in game play which purpose would be to disrupt the Service or to commit fraud." Also bans "any robot, spider, crawler, scraper, or other automated means or interface not provided by us". The official API counts as an interface provided by SX. — [SX Bet Terms and Conditions](https://help.sx.bet/en/articles/3613372-terms-and-conditions)
- SX publishes its own developer content on building bots and aggregators against its API, e.g. an SX-vs-Polymarket aggregator with greedy routing. — [SX Bet blog: Sports Prediction Market Aggregator](https://blog.sx.bet/developers/builds/sports-prediction-market-aggregator/)

**Limitless Exchange (Base)**
- REST `https://api.limitless.exchange` and WebSocket `wss://ws.limitless.exchange`, on Base mainnet (chain 8453). There is no testnet, and the docs suggest testing with small live orders. — [Limitless developer intro](https://docs.limitless.exchange/developers/introduction)
- Individual traders create a scoped API token (ID + secret) in the UI under profile → Api keys, with "no application needed". Requests are HMAC-SHA256-signed with the `lmts-api-key`, `lmts-timestamp` and `lmts-signature` headers. Orders are EIP-712 signed and posted to `POST /orders`. Accounts that ever enabled 1-click smart-wallet trading must switch to EOA mode before placing API orders. Partner integrations (sub-accounts, delegated signing) need a separate application. — [Limitless developer intro](https://docs.limitless.exchange/developers/introduction)
- Official SDKs exist for Python, TypeScript, Go and Rust. There is also a "Migrate from Polymarket" guide, a "Build a trading agent" guide and a trading MCP server. — [Limitless developer intro](https://docs.limitless.exchange/developers/introduction)
- The ToS (last updated Sept 15, 2026) does not mention bots, automated trading or the API. — [Limitless Terms of Service](https://docs.limitless.exchange/user-guide/terms-of-service)
- The TS SDK README says Limitless restricts order placement from US locations. — [@limitless-exchange/sdk on npm](https://www.npmjs.com/package/@limitless-exchange/sdk)

**Azuro protocol (bookmaker.xyz and other frontends)**
- V3 bet flow: (1) approve the token to the Relayer contract; (2) sign the bet parameters with EIP-712; (3) "send the signed data to the oracle via API"; (4) the oracle co-signs and passes the order to the Relayer Executor; (5) the executor calls `betFor(OrderData[])` on-chain. The bettor receives an NFT-like bet token, and winnings are claimed via `LP.withdrawPayout`. — [Azuro docs: Bet functions](https://gem.azuro.org/hub/blockchains/bet-functions)
- Official npm packages are `@azuro-org/sdk`, `@azuro-org/toolkit` and `@azuro-org/dictionaries`, alongside public repos for Smart Contracts, Liquidity Tree and Access. — [Azuro docs landing](https://gem.azuro.org/)
- The older `@azuro-protocol/sdk` (Gnosis-era, possibly outdated) exposed `placeBet(conditionId, outcomeId, amount, betRate, slippage)` and `calculateActualOdds`. — [npm @azuro-protocol/sdk](https://npmjs.com/package/@azuro-protocol/sdk)
- A third-party agent "skill" places Azuro bets on Polygon by reading the REST data feed and bets subgraph, then signing with EIP-712 and submitting through a frontend (Pinwin). This shows bot betting is technically routine. — [clawhub sports-betting skill](https://clawhub.ai/skinnynoizze/sports-betting)
- The bookmaker.xyz ToS (dated "Last updated: August 09, 2024", with a ©2026 footer) lists among fraudulent activities "use of 'bots' – automatized players". It also bans scraping "data mining tools, robots, crawlers", says the smart contract may block a wallet "if you are using prohibited software", and states: "You shall not use any VPN, proxy, or other similar services to override the country's technical restrictions." — [bookmaker.xyz Terms](https://bookmaker.xyz/docs/terms)
- `NewLiveBet` events carry an indexed `affiliate` field, so bets are attributed to a frontend/affiliate address. The docs do not state whether a given affiliate is required. — [Azuro docs: Bet functions](https://gem.azuro.org/hub/blockchains/bet-functions)

**Overtime (Thales; Optimism / Arbitrum / Base)**
- The API base is `https://api.overtime.io`. Access is limited to approved API keys issued to teams or organizations with a concrete use case. The docs say the API is not intended for "experimental, personal, arbitrage or any sorts of automated trading projects." — [Overtime V2 integration](https://docs.overtime.io/overtime-v2-integration.md)
- Trading is done on-chain via `SportsAMMV2.trade(tradeData, buyIn, totalQuote, slippage, referrer, collateral, bool)`. `tradeData` must include odds and a `merkleProof` from the API's `proof` field, so the gated API is effectively needed to trade. Live bets use `requestLiveTrade` on a Live Trading Processor contract, then poll `requestIdToFulfillAllowed`. A trade fails if odds move during fulfillment. — [Overtime V2 integration](https://docs.overtime.io/overtime-v2-integration.md)
- The FAQ says Overtime is "deployed on Optimism, Arbitrum and Base". — [Overtime FAQ](https://docs.overtime.io/get-started/faq.md)
- The ToS has no clause on bots or automated access. — [Overtime Terms of Use](https://docs.overtime.io/resources/terms-of-use.md)

**Myriad Markets (Abstract / Linea / BNB)**
- Secondary sources say Myriad uses Polkamarkets smart contracts and offers a REST API and JavaScript SDK. — [MEXC/HackerNoon article on Myriad Protocol](https://www.mexc.com/en-GB/news/building-decentralized-prediction-markets-across-three-blockchains-with-myriad-protocol/127385)
- It markets an "API + SDK + CLI stack", revenue sharing for integrators and AI-agent support. — [FinFeedAPI: Myriad explained](https://www.finfeedapi.com/blog/myriad-markets-explained)
- I found no official Myriad API reference; see Gaps.

**Other venues**
- **DFlow (Kalshi markets tokenized on Solana):** the Prediction Markets API went live in Dec 2025. A Metadata API handles discovery, and a Trade API returns ready-to-sign Solana transactions to buy or redeem outcome SPL tokens. The example workflow trades live English Premier League markets. — [QuickNode guide: Kalshi prediction markets with DFlow](https://www.quicknode.com/guides/solana-development/3rd-party-integrations/kalshi-prediction-markets-with-dflow); [The Block](https://www.theblock.co/post/380983/kalshi-tokenizes-thousands-of-its-prediction-markets-using-solana); [DFlow docs](https://pond.dflow.net/prediction-markets/introduction)
- **Predict.fun (BNB Chain):** EIP-712 signing with a BNB wallet plus a Discord-issued API key. Public testnet reads need no auth. — [OddsPapi: Prediction Market API 2026 stack (May 7, 2026)](https://oddspapi.io/blog/prediction-market-api-2026-stack/)
- **Hyperliquid HIP-4 outcome markets:** mainnet went live May 2, 2026 with only daily BTC threshold binaries. Markets are fully collateralized 0/1 contracts on Hyperliquid's order book. — [BlockEden, May 3 2026](https://blockeden.xyz/blog/2026/05/03/hyperliquid-hip-4-mainnet-launch-day-one-prediction-market/); [BlockEden deep dive, May 13 2026](https://blockeden.xyz/blog/2026/05/13/hyperliquid-hip4-prediction-market-deep-dive/)

### Inferences
- For a bot that must place bets **legitimately**, the ranking is: SX Bet (bot-first API, makers and takers, heartbeat, cancel-all), then Limitless (Polymarket-like CLOB API with self-serve keys), then Azuro (permissionless contracts and public order API, but each frontend has its own ToS and bookmaker.xyz bans bots). Overtime is effectively excluded: its docs explicitly exclude arbitrage and automated trading from API access, and trading needs API-supplied merkle proofs.
- Azuro sits in a grey zone. The protocol is meant to be integrated programmatically: the SDK, public backend and permissionless relayer exist so that builders can create their own apps. A user calling the protocol through the official SDK, as their own integrator, is arguably not bound by bookmaker.xyz's ToS. But they would also have no ToS-defined relationship that clearly permits it. Treat this as uncertain, not as "allowed".
- Limitless has a "migrate from Polymarket" path, so it is the lowest-effort integration for a codebase already speaking Polymarket CLOB/EIP-712.

### Gaps
- No official Myriad API reference or SDK package name was found (only secondary descriptions). Polkamarkets GitHub repos exist ([Polkamarkets GitHub org](https://github.com/Polkamarkets/)), but I could not confirm which ones Myriad uses today.
- Azuro: no official statement found on whether third parties may bet directly via the API without a frontend affiliate, or on any per-wallet bot policy at the protocol level.
- Azuro: no confirmation of which frontend ToS (besides bookmaker.xyz) allows bots.
- No DFlow terms or restricted-country list was checked.

---

## 2. Can odds/order books be read for free (no auth or free registration)? Endpoints, rate limits, websockets

### Takeaway
SX Bet, Azuro and Limitless all expose **free, keyless public read access** with websockets. SX Bet is the best documented, with explicit per-IP rate limits. Overtime's market and odds endpoints are "protected" (they need an approved API key).

### Cited Findings

**SX Bet**
- Public read endpoints are `GET /orderbook-v3/snapshot?marketHash=…` (with `showTakerPerspective=true`) and `GET /orders-v3/odds/best`. Realtime channels are `orderbook_v3:{marketHash}`, `orderbook_v3_event:{eventId}` and `best_odds_v3:global`; they publish the full aggregated book, applied by `version`. — [SX Bet docs](https://docs.sx.bet/)
- Odds format: `percentageOdds` is implied probability × 10^20, and the taker's probability is 1 − the maker's. The default ladder step is 0.125% (`oddsLadderStepSize`). The public book shows aggregated levels, not individual orders. — [SX Bet docs](https://docs.sx.bet/); [New in V3](https://docs.sx.bet/developers/new-in-v3)
- Public limits per client IP: `/orderbook-v3/snapshot` 27,500 per 300 s; `/orders-v3/odds/best` 5,500/60 s; `/metadata/obv3` 500/60 s; `/trades-v3/public` 200/60 s. `/markets/*`, `/fixture/*`, `/leagues`, `/sports`, `/teams`, `/live-scores` and `/search` share one budget of 500/60 s. All unauthenticated traffic shares one edge bucket of 35,000 requests per 10 min. Realtime subscriptions do not count against the request budget. — [SX Bet rate limits](https://docs.sx.bet/developers/rate-limits)
- A third-party summary says the REST API is public and free and needs no credentials to read market data. — [skills.sh SX Bet summary](https://skills.sh/site/docs.sx.bet/sx-bet)

**Azuro**
- Backend REST: production `https://api.onchainfeed.org/api/v1/public`, dev `https://dev-api.onchainfeed.org/api/v1/public`. "Most endpoints in the Backend API do not require authentication." It provides the feed (sports, games, conditions, odds), bet calculation (odds, limits, payouts), order processing and cashout. — [Azuro Backend API](http://gem.azuro.org/hub/apps/APIs/backend)
- WebSocket: production `wss://streams.onchainfeed.org/v1/streams/feed`, dev `wss://dev-streams.onchainfeed.org/v1/streams/feed`, for real-time updates of games and conditions. — [Azuro WebSocket API](http://gem.azuro.org/hub/apps/APIs/websocket)
- Historical data and bets are indexed by subgraphs on The Graph. — [Azuro docs landing](https://gem.azuro.org/); [Azuro APIs](http://gem.azuro.org/hub/apps/APIs)
- Environment IDs and bet tokens: Gnosis = XDAI, Polygon = USDT, Chiliz = WCHZ, Base = WETH, plus testnets (PolygonAmoy, ChilizSpicy, BaseSepolia, GnosisDev). — [Azuro APIs](http://gem.azuro.org/hub/apps/APIs)

**Limitless**
- REST and WebSocket offer real-time market and orderbook streaming. — [Limitless developer intro](https://docs.limitless.exchange/developers/introduction)
- Third-party write-ups describe a keyless public REST with `/v1/markets`, `/v1/market` and `/v1/tags` (unverified against the official reference). — [oanor Limitless API llms.txt](https://oanor.com/api/limitless-api/llms.txt)
- CCXT has a Limitless connector (`fetchMarkets` with search queries, `fetchEvent`). — [CCXT Limitless docs](https://docs.ccxt.com/docs/prediction/limitless)

**Overtime**
- Markets, live markets, liquidity, user history and quote-data endpoints are marked "(protected)". Sports, market types, collaterals, games info, players info and live scores are not marked protected. Example: `GET /overtime-v2/networks/{networkId}/markets?leagueId=50&ungroup=true`. — [Overtime docs index](https://docs.overtime.io/llms.txt); [Overtime V2 integration](https://docs.overtime.io/overtime-v2-integration.md)

**Aggregators**
- OddsPapi resells normalized odds for Limitless, Myriad, SX Bet and Polymarket. Azuro and Drift are not on OddsPapi. — [OddsPapi: Prediction Market API 2026 stack](https://oddspapi.io/blog/prediction-market-api-2026-stack/); [OddsPapi Myriad page](https://oddspapi.io/sportsbooks/myriad)

### Inferences
- SX Bet's public limits (e.g. 5,500 best-odds calls/min per IP, plus free websocket channels) comfortably support a VPS scanner. The `best_odds_v3:global` channel alone can replace polling.
- Azuro's public feed and websocket make it the second-easiest venue to scan for free. Odds are oracle-pushed, so the websocket shows when the data provider updates.
- Overtime cannot be scanned legitimately for odds at scale without an approved key. Its on-chain state lacks the off-chain odds and merkle trees.

### Gaps
- Azuro backend and websocket rate limits are undocumented on the pages fetched. Subscription message formats were also not retrieved.
- No official Limitless rate-limit page was retrieved.
- No official Myriad read API was found. OddsPapi is a paid third-party option.

---

## 3. Sports and market types, liquidity, and how odds are set (AMM, oracle-fed or P2P), including any margin added by a data provider

### Takeaway
Two pricing families matter for arbitrage against Polymarket:
- **P2P order books (SX Bet, Limitless, HIP-4).** Prices come from traders. SX shows tight prices on match winners and offers spreads and totals.
- **Oracle-fed house/AMM books (Azuro, Overtime).** A data provider pushes odds (Pinnacle-derived for Overtime) and a margin is added on top: Overtime's protocol fee is 2%, and Azuro's take rate averaged about 4.3% (2024).

The oracle-fed books can lag fast-moving Polymarket prices, but both reserve the right to void "obviously" wrong odds.

### Cited Findings

**SX Bet**
- It is a P2P order book. Maker liquidity rests via GTC, and takers cross via IOC/FOK. — [SX Bet docs](https://docs.sx.bet/)
- A single-day snapshot (≈ May 1, 2026, by an SX team member; 64 match-winner outcomes and 768 total outcomes starting within 24 h) found SX had the better price on 88% of match-winner lines and 89% of all outcomes vs Polymarket. Average liquidity above the other venue was $2.4k on match winners and $1.7k overall. Polymarket "can't offer whole lines", and its spread and total coverage "falls off significantly". — [SX Bet blog aggregator](https://blog.sx.bet/developers/builds/sports-prediction-market-aggregator/)
- OddsPapi (May 2026) says SX coverage is consistent on major soccer, MLB, NHL, tennis and MMA, but thin on niche leagues. — [OddsPapi stack](https://oddspapi.io/blog/prediction-market-api-2026-stack/)
- Vendor claims (unverified): "international soccer or smaller markets can have $200 to $500 of depth at the best price". Live markets are "significantly thinner" than pregame. Liquidity is thin outside the top 20 markets. — [Claw Arbs SX Bet guide, May 7 2026 (vendor)](https://clawarbs.com/blog/sx-bet-arbitrage/)
- Historical volume (old): $38.8M betting volume in Q3 2024, up 56% YoY. Sports volume is highly seasonal. — [SX Weekly #76 Q3 review (2024)](https://sxweekly.substack.com/p/sx-76-q3-review)

**Azuro**
- Odds = total Condition funds ÷ outcome Virtual Funds. A data provider supplies initial odds, and "initial reinforcement" is split to match them. Each bet adds to that outcome's virtual fund, which moves the odds (a vAMM). A profit margin is set at condition creation through outcome-specific spreads (margin = 1 − 1/Σ(1/oᵢ)), and payout = oᵢ × (1 − sᵢ) × stake. — [Azuro docs: Odds](https://gem.azuro.org/knowledge-hub/how-azuro-works/components/odds)
- Liquidity is a single shared LP pool (the "Liquidity Tree") that every market draws from. — [Azuro docs landing](https://gem.azuro.org/); [BeInCrypto Azuro guide](https://beincrypto.com/learn/azuro-guide/)
- Data providers are entities elected by AzuroDAO, and margin is part of each market's information packet. Pinnacle Solution (Pinnacle's B2B arm) made its "lowest margin" sports and esports feed available to Azuro builders in 2022. — [BeInCrypto Azuro guide](https://beincrypto.com/learn/azuro-guide/); [SBC News, Aug 2022](https://sbcnews.co.uk/technology/2022/08/09/pinnacle-solution-azuro/)
- The average take rate was 4.28%. As of June 2024 the protocol had seen $358M in cumulative prediction volume and $3.4M in revenue (outdated). — [DappRadar Azuro ecosystem report (Jun 2024)](https://dappradar.com/blog/azuro-protocol-ecosystem-report)
- 2026 revenue figures conflict: DefiLlama shows about $465.68K gross revenue in Q2 2026, while Tokenomics shows $632.2K for 2026. — [DefiLlama AZUR](https://defillama.com/token/AZUR); [Tokenomics Azuro revenue](https://app.tokenomics.com/tokenomics/azuro/revenue)
- Markets include football 1X2 (settled at 90' + injury time), double chance, and basketball/NFL/MLB full-game markets including overtime. Hockey, handball and rugby settle on regular time only. Esports and cricket are also offered. — [Azuro Rules & Settlement Policy](https://gem.azuro.org/knowledge-hub/additional/rules-and-settlements-policy)
- Contradiction: a 2024 Gate/TechFlow article says Azuro odds use LMSR and that its AMM "lags" SX's order book. The official docs describe a virtual-funds vAMM, not LMSR. — [Gate Learn: Polymarket, SX Bet, PredX, Azuro analysis](https://www.gate.com/learn/articles/in-depth-analysis-of-polymarket-sx-bet-pred-x-and-azuro-prediction-markets/3936); contradicted by [Azuro docs: Odds](https://gem.azuro.org/knowledge-hub/how-azuro-works/components/odds)

**Overtime**
- Odds sources: Pinnacle odds via The Rundown's API, then Chainlink to the chain; JsonOdds for golf (TIP-150); OpticOdds push feed (TIP-207). The AMM prices positions at the provider's implied probability. — [Overtime: About Odds Providers](https://docs.overtime.io/learn-about-overtime/about-odds-providers.md)
- A protocol fee "currently set at **2%**" sits on top of provider odds. Each market has a predefined risk limit (maximum directional exposure), and the AMM stops offering a side once its limit is hit. Paying with $OVER gives a reduced margin, or +1% payout per game per the API docs. — [Overtime AMM & liquidity](https://docs.overtime.io/learn-about-overtime/overtime-amm-and-liquidity-mechanics.md); [Overtime V2 integration](https://docs.overtime.io/overtime-v2-integration.md)
- Under OIP-265 (June 2026), "New external deposits into the Overtime liquidity pools are closed". The treasury is now the sole LP. — [Overtime AMM & liquidity](https://docs.overtime.io/learn-about-overtime/overtime-amm-and-liquidity-mechanics.md)
- Quote responses include `ticketLiquidityInUsd`, i.e. max available per ticket. — [Overtime V2 integration](https://docs.overtime.io/overtime-v2-integration.md)
- Volume (outdated): users had wagered over $27M (article more than 2 years old). — [DL News](https://www.dlnews.com/articles/web3/users-can-be-the-house-on-sports-betting-platform-overtime/)

**Limitless**
- It runs CLOB and NegRisk markets. — [Limitless developer intro](https://docs.limitless.exchange/developers/introduction)
- Origin: hourly and daily crypto and stock price markets. — [Limitless docs intro](https://docs.limitless.exchange/)
- Sports include international soccer (World Cup); its X bio lists Crypto, Finance, Football, Esports. — [parse.gl Limitless profile](https://www.parse.gl/brands/limitless-exchange)
- Volume rose from $204.9M in Jan 2026 to $1.66B in Apr 2026 (all categories; tracker figure). It filed for a CFTC DCM (Limitless Markets US, LLC). — [DeFi Rate](https://defirate.com/?p=10798)
- CEO (Sept 23, 2026): Limitless "has just started aggregating liquidity" from Kalshi and Polymarket on its exchange. Its onchain product serves non-US users, mainly APAC and Europe. It launched player-vs-player in-match markets (corner, foul, goal). — [Scuttlebutt interview summary](https://getscuttlebutt.substack.com/p/limitless-exchange-ceo-us-sports)
- OddsPapi (May 2026): "deep on crypto and political; growing on headline sports". — [OddsPapi stack](https://oddspapi.io/blog/prediction-market-api-2026-stack/)

**Myriad**
- It runs an AMM ("sum of all outcome prices always equals $1"), and a newer order book with limit orders on some markets. — [FinFeedAPI](https://www.finfeedapi.com/blog/myriad-markets-explained)
- It launched March 2025 by DASTAN (Decrypt + Rug Radio) on Abstract, later Linea and BNB Chain. Sports is one category among crypto, politics and culture. — [QuickNode Myriad listing](https://www.quicknode.com/builders-guide/tools/myriad-markets-by-dastan)
- OddsPapi: "growing on headline sports". — [OddsPapi stack](https://oddspapi.io/blog/prediction-market-api-2026-stack/)

**Drift BET (Solana)**
- Launched Aug 19, 2024 with two US-election contracts. Sports were only "planned". — [The Block](https://www.theblock.co/post/311888/solana-based-drift-protocol-launches-prediction-market); [Decrypt](https://decrypt.co/245565/new-solana-based-polymarket-rival-from-drift-draws-over-3-million-in-liquidity?amp=1)
- Drift suffered an approximately $270–285M exploit on April 1, 2026, attributed to DPRK-linked actors via admin-key compromise. TVL collapsed to about $24M, and deposits and withdrawals were halted. — [TRM Labs](https://trmlabs.com/resources/blog/north-korean-hackers-attack-drift-protocol-in-285-million-heist); [Unchained](https://unchainedcrypto.com/drift-protocol-suffers-285-million-exploit-after-admin-key-compromise-and-oracle-manipulation-unchained/); [4pillars](https://research.4pillars.io/en/research/reflections-on-the-drift-protocol-exploit)

**Hedgehog, Zeitgeist, Polkamarkets**
- Hedgehog (Solana and Eclipse SVM) runs P2P escrow and parimutuel pools, and sports is among its categories. It is listed as "Healthy" by an aggregator in Aug 2026, but there is no adoption data. — [Solana Compass](https://solanacompass.com/projects/hedgehog); [MadeOnSol comparison](https://madeonsol.com/compare/dexsport-vs-hedgehog-markets)
- Zeitgeist (Polkadot parachain) is still described as live, but its GitHub showed 0 commits in the prior 30 days, and "Polkadot Ecosystem Decline" is flagged as a risk. — [pm.wiki Zeitgeist](https://pm.wiki/uk/projects/zeitgeist)
- Polkamarkets is described as a Moonbeam/Ethereum protocol and powers Myriad's contracts. — [pm.wiki Polkamarkets vs Zeitgeist](https://pm.wiki/es/compare/polkamarkets-vs-zeitgeist); [MEXC/HackerNoon](https://www.mexc.com/en-GB/news/building-decentralized-prediction-markets-across-three-blockchains-with-myriad-protocol/127385)

**Newer venues**
- **Hyperliquid HIP-4:** sports were planned ("Phase 2 opens in mid-June" around the World Cup), but sources from May–June 2026 show only BTC daily binaries live. Outcome.xyz is the first builder, at about $1.5M in builder volume. — [BlockEden](https://blockeden.xyz/blog/2026/05/13/hyperliquid-hip4-prediction-market-deep-dive/); [KuCoin flash](https://www.kucoin.com/news/flash/hyperliquid-hip-4-deployment-begins-outcome-becomes-first-builder)
- **World (Solana, inside the Phantom wallet):** launched July 1, 2026 with crypto and 2026 FIFA World Cup contracts. It settles in Phantom's CASH stablecoin with a Chainlink oracle. API and fees are not stated. — [CoinDesk](https://www.coindesk.com/web3/2026/07/01/mysterious-solana-project-world-unveiled-as-fully-onchain-prediction-market)
- **BetDEX / Monaco Protocol (Solana sports exchange):** aggregator pages (Aug–Sep 2026) list a 3% fee on net winnings (1% Monaco + 2% BetDEX). Mainnet and licensing status are unconfirmed, and the detailed reporting is from about 2022. — [MadeOnSol BetDEX vs Polymarket](https://madeonsol.com/compare/betdex-vs-polymarket); [Blockworks (older)](https://blockworks.com/news/fanduel-execs-bitdex-bet-crypto-can-disrupt-sports-gambing)
- **DFlow/Kalshi on Solana:** covers Kalshi's sports markets (e.g. EPL) as SPL tokens. — [QuickNode DFlow guide](https://www.quicknode.com/guides/solana-development/3rd-party-integrations/kalshi-prediction-markets-with-dflow)

### Inferences
- **Oracle-fed books as an arbitrage source:** Overtime (Pinnacle/OpticOdds + 2%) and Azuro (data provider + spread, roughly 4% take) post sportsbook-like prices with built-in vig. An arb against Polymarket appears mostly when Polymarket deviates from the sharp consensus (Pinnacle), or when the oracle push lags a fast move, for example live or around lineup news. Azuro's vAMM also shifts odds after each bet, so the quoted price changes with stake size and with others' flow.
- **SX Bet is the strongest natural pair:** the most liquid crypto P2P sports book, with spreads and totals that Polymarket lacks. Being an order book, though, it tends to sit close to Polymarket on headline moneylines, so edges are thin and fleeting.
- **Limitless is now partially aggregating Polymarket liquidity.** If that means Polymarket books are mirrored or routed, cross-venue gaps between the two will shrink by construction. Verify before investing effort.
- Drift BET, Zeitgeist, Polkamarkets and (as far as could be confirmed) HIP-4 sports are **not viable** candidates in Oct 2026. Hedgehog, World and BetDEX are unverified or too thin.

### Gaps
- No 2026 per-sport liquidity or volume data for SX Bet, Azuro or Overtime from a primary dashboard. DefiLlama and Dune pages returned 403 or were not found.
- Azuro's exact current margin per sport and live-bet acceptance delay are not documented in the pages fetched.
- Overtime has no numeric max bet or risk limit; it is only exposed via `ticketLiquidityInUsd` in protected quotes.
- No confirmation that HIP-4 sports markets went live after June 2026.
- Myriad sports volume is unknown.

---

## 4. Fees/commissions/gas and settlement/resolution rules (result sources, void rules)

### Takeaway
SX Bet charges per-account fees **on profit only** (wins; never losses or voids), and live taker orders face a configurable delay. Overtime embeds a 2% fee in the odds. Azuro embeds the margin via spreads, and some frontends are gasless. Settlement rules differ materially across venues (e.g. Azuro voids football not played within 48 h, Overtime within 24 h), which is a real arbitrage leg-mismatch risk against Polymarket.

### Cited Findings

**SX Bet**
- Fees are `makerPayoutFee` and `takerPayoutFee` for singles, plus parlay equivalents. They apply to profit, are charged at settlement on winning bets only, and "a loss and a void are never charged". A separate `refundFee` applies to the capital-efficiency refund. Rates are per account, via `GET /user/fees-v3`. — [SX Bet Fees](https://docs.sx.bet/developers/fees)
- Conflict: a vendor blog claims "0% on single bets, 5% on winning parlays". The user's own observation is a 1% taker fee on profit, so treat the vendor figure as unreliable or outdated; per-account rates explain why it can differ. — [Claw Arbs (vendor)](https://clawarbs.com/blog/sx-bet-arbitrage/)
- Betting delay (milliseconds) comes from `GET /metadata/obv3` → `data.bettingDelay`. Live delay resolves `leagueIdToMs` → `sportIdToMs` → `liveMsDefault`, and prematch uses `pregameMsDefault`. The delay applies **only to taker orders**; makers go ACTIVE immediately. A delayed order matches against the book as it stands when the delay ends, so it may fill partially or not at all. "Live" means `gameTime` has passed. — [SX Bet Betting delays](https://docs.sx.bet/developers/betting-delays)
- Collateral is USDC (6 decimals) via `activeAsset.baseToken`. Funding uses permit-based transfers to the proxy, with deposit types FROM_EOA, BRIDGE and PROXY_TO_PROXY. — [SX Bet Funding](https://docs.sx.bet/developers/funding)
- Chain: SX Bet migrated to "SX Rollup", an Arbitrum Orbit L2, in Aug 2024. — [SX Weekly #73](https://sxweekly.substack.com/p/sx-73-rollup-migration)
- OddsPapi (May 2026) says "USDC on Polygon", which conflicts. The V3 docs fetched do not name the chain. — [OddsPapi stack](https://oddspapi.io/blog/prediction-market-api-2026-stack/)
- The ToS mentions an "SX Token Sunset" program with a snapshot as of May 15, 2026 and an acceptance deadline of Aug 15, 2026; allocations are subject to KYC and sanctions screening. — [SX Bet Terms](https://help.sx.bet/en/articles/3613372-terms-and-conditions)

**Azuro**
- Settlement uses "official results confirmed by official sources". In conflicts, "the official broadcast/source as defined by the feed provider" has priority. — [Azuro Rules & Settlement](https://gem.azuro.org/knowledge-hub/additional/rules-and-settlements-policy)
- Football not played "within 48 hours from the original scheduled time" is voided at odds of 1.00. Most sports void if not completed within 36–48 h. — [Azuro Rules & Settlement](https://gem.azuro.org/knowledge-hub/additional/rules-and-settlements-policy)
- "Bets may be voided if placed on markets with clearly incorrect odds or status due to technical or data feed errors." — [Azuro Rules & Settlement](https://gem.azuro.org/knowledge-hub/additional/rules-and-settlements-policy)
- Bets resolve on average within 2 hours of the event's end. Some apps offer gasless betting. — [Azuro Betting FAQ](https://gem.azuro.org/knowledge-hub/faqs/betting)
- Bettor losses are split among the DAO, LPs, frontends and data providers. — [BeInCrypto Azuro guide](https://beincrypto.com/learn/azuro-guide/)

**Overtime**
- The 2% protocol fee is embedded in odds. Half of fees from partner-driven trading go to the integrator. — [Overtime AMM](https://docs.overtime.io/learn-about-overtime/overtime-amm-and-liquidity-mechanics.md)
- Results come from "Chainlink nodes and designated providers (OpticOdds etc.)", which are the "final authority". — [Overtime Sports Trading Guidelines](https://docs.overtime.io/learn-about-overtime/sports-trading-guidelines.md)
- Void rules: a walkover or no-contest voids the fixture. A fixture not started within 24 h with no rescheduled time may be voided. 2-way markets are void on a draw. — [Overtime Sports Trading Guidelines](https://docs.overtime.io/learn-about-overtime/sports-trading-guidelines.md)
- "Obvious odds errors may be cancelled". Prematch bets placed after kickoff may be voided if odds were "materially incorrect". In-play orders "may queue briefly or pend during high-risk moments". Settlements are final after 72 h. — [Overtime Sports Trading Guidelines](https://docs.overtime.io/learn-about-overtime/sports-trading-guidelines.md)

**Limitless**
- Shares trade at $0.01–$0.99 USDC and winners pay $1. — [Limitless docs intro](https://docs.limitless.exchange/)
- The ToS reserves the right to charge and modify "market creation fees, trading fees"; fees are non-refundable. — [Limitless ToS](https://docs.limitless.exchange/user-guide/terms-of-service)

**Myriad**
- Buy fees are typically 0–2%, plus about $0.0085 gas. It is non-custodial (Thirdweb wallets) and typically funded and withdrawn in USD1 on BNB Chain. — [FinFeedAPI (promotional)](https://www.finfeedapi.com/blog/myriad-markets-explained)

**Polymarket (comparison leg)**
- An SX blog post (May 2026) models Polymarket's sports taker fee as `p + feeRate·p·(1−p)` with a 3% feeRate: about 0.75% of notional at 50/50, 0.48% at 80/20 and 0.14% at 95/5. It argues this "largely removes" Polymarket from 50–200 bps arbs. — [SX Bet blog](https://blog.sx.bet/developers/builds/sports-prediction-market-aggregator/)
- Conflict: a vendor blog says Polymarket charges "0% plus gas", which is likely outdated. — [Claw Arbs](https://clawarbs.com/blog/sx-bet-arbitrage/)

### Inferences
- In surebet math, SX's fee should be modeled as `fee × profit` on the winning leg only, using the per-account rate from `/user/fees-v3`, never a constant. Overtime and Azuro need no fee term because the margin is in the quoted odds. Polymarket needs its probability-dependent taker fee.
- Settlement mismatch is a concrete risk for any pair. Examples: a postponed football match (Azuro voids after 48 h, Overtime after 24 h, while Polymarket resolves by its own market rules), soccer 1X2 at 90', and hockey regular time vs including OT. A scanner should only pair markets with identical settlement scope.
- "Obvious error" void clauses at Azuro and Overtime mean that stale-oracle arbs, the exact edge that oracle-fed books offer, carry cancellation risk on the soft leg.

### Gaps
- No actual numeric SX `bettingDelay` values or current default fee rates were retrieved (they must be read live from `/metadata/obv3` and `/user/fees-v3`).
- SX settlement chain in V3 is unconfirmed (SX Rollup vs Polygon conflict).
- Azuro relayer fee and gas payer for direct API bets are not documented on the pages fetched.
- No confirmed Polymarket sports fee parameters from Polymarket's own docs; the source is SX's blog.

---

## 5. Geographic restrictions (Venezuela and Colombia) and KYC

### Takeaway
**SX Bet's current Terms explicitly list Venezuela as a restricted country.** Colombia is not named, but it is covered by a catch-all ("any other country which may prohibit the offering of online gambling to its residents"). The **bookmaker.xyz (Azuro)**, **Overtime** and **Limitless** terms list neither Venezuela nor Colombia. Bookmaker.xyz has the same kind of catch-all and an explicit anti-VPN clause. None of these venues has a general KYC requirement in its ToS. Limitless reserves the right to request identity proof.

### Cited Findings
- **SX Bet:** "You are not a resident of the following countries/regions:" includes Ontario, Australia, Austria, France, Germany, the Netherlands, Spain, the UK, the USA, Russia, Iran, Iraq, Syria, Cuba, Belarus, Myanmar, Sudan, South Sudan, Zimbabwe, Afghanistan, Yemen, North Korea, **Venezuela**, Comoros, "and any other country which may prohibit the offering of online gambling to its residents." Colombia is not listed. There is no VPN clause and no general KYC clause. The owner is "Nextgen Blockchain Technologies LTD.", and "The laws of Costa Rica govern the Services." — [SX Bet Terms and Conditions](https://help.sx.bet/en/articles/3613372-terms-and-conditions)
- SX Bet licensing is described inconsistently by third parties: an Anjouan licence (investinglive), CRGC Management Ltd per an earlier version of the terms, or Sunseven N.V. Curaçao (VIP-IBC), each with different restricted lists. — [investinglive](https://investinglive.com/directory/prediction-market-platforms/sx-bet); [VIP-IBC](https://vip-ibc.com/services/sx-bet/)
- **bookmaker.xyz (Azuro frontend):** the restricted list (clause 4) is Azerbaijan, Afghanistan, Bahamas, Bangladesh, Bahrain, Benin, Bolivia, Brazil, Burkina Faso, Burundi, China, Vietnam, Gambia, Hong Kong, Iraq, Iran, Yemen, Jordan, Kazakhstan, Cayman Islands, Cambodia, Qatar, Kyrgyzstan, Cuba, Kuwait, Indonesia, Lebanon, Libya, Myanmar, UAE, Oman, Pakistan, Russia, Saudi Arabia, Singapore, Syria, Sudan, Thailand, Tajikistan, Trinidad and Tobago, Tunisia, Turkmenistan, Turkey, Uzbekistan, Central African Republic, **Ecuador**, South Korea, Japan, USA, UK "and any other country where online gambling is considered illegal". **Venezuela and Colombia are not listed.** The terms also contain an explicit anti-VPN clause and no KYC clause. The operator is "THE DAO OF THE BOOKMAKER.XYZ", and the terms were last updated Aug 9, 2024. — [bookmaker.xyz Terms](https://bookmaker.xyz/docs/terms)
- **Overtime:** the "Prohibited Jurisdictions" are the US and its territories, Belarus, Burma, China, Cuba, DR Congo, Iran, Iraq, Liberia, North Korea, Sudan, Syria and Zimbabwe, plus OFAC SDN-listed persons. Venezuela and Colombia are not mentioned. There are no VPN, KYC or bot clauses. — [Overtime Terms of Use](https://docs.overtime.io/resources/terms-of-use.md)
- **Limitless (ToS updated Sept 15, 2026):** access is blocked from Russia, Belarus, Cuba, Iran, North Korea, Syria, Crimea, Donetsk and Luhansk. Trading is blocked for the USA, the "Republic of China", Ontario and Alberta. Venezuela and Colombia are not mentioned. "The Company may require additional verification of Your identity at any time." There is no VPN clause. — [Limitless ToS](https://docs.limitless.exchange/user-guide/terms-of-service)
- An earlier version of the Limitless terms named the operator as Street Chow Inc. (Panama). — [search-surfaced Limitless ToS (Jun 19, 2026 version)](https://docs.limitless.exchange/user-guide/terms-of-service)
- **Myriad:** its site says "There are Points and USD₮ markets available, depending on your country". The Terms of Use page (Notion) could not be read. — [myriad.markets/terms](https://myriad.markets/terms)
- An aggregator lists Myriad's geography as "global". — [pm.wiki Myriad comparison](https://pm.wiki/id/compare/magicmarkets-vs-myriad)

### Inferences
- **SX Bet is contractually off-limits to Venezuelan residents.** A Colombian resident is not named, but the catch-all clause could apply depending on how Colombian law treats unlicensed offshore gambling. That is a legal question to verify, not something these sources settle. This is a significant finding, since the user already uses SX Bet.
- Among the candidates, **Overtime and Limitless** have ToS with no VE or CO restriction. Overtime's API, however, excludes arbitrage bots. **bookmaker.xyz** does not list VE or CO, but its ToS bans bots and VPN use, and its catch-all covers countries where online gambling is illegal.
- Because ToS lists change without notice (bookmaker.xyz says so explicitly), the bot should re-check them periodically.

### Gaps
- Myriad's restricted-country list was not retrieved.
- DFlow/Kalshi-on-Solana and Hyperliquid HIP-4 eligibility for VE and CO were not checked.
- The ToS of other Azuro frontends (beyond bookmaker.xyz) were not checked.
- Whether Colombian law (Coljuegos licensing regime) makes SX Bet's or bookmaker.xyz's catch-all clause apply to Colombian residents: no source was found in this research.
- Polymarket's own VE/CO status is outside this scope.

---

## 6. Evidence of arbitrage between these venues and Polymarket or sportsbooks (articles, bots, dashboards)

### Takeaway
Evidence exists but is mostly vendor or self-interested. SX's own snapshot shows SX beating Polymarket on 88% of match-winner prices, and argues Polymarket's taker fee eats 50–200 bps arbs. Vendor blogs claim SX lags Polymarket by 30–90 s after big moves. No open-source Azuro- or Overtime-vs-Polymarket arbitrage bots were found. Arbitrage between Polymarket and sportsbooks generally is widely discussed.

### Cited Findings
- SX team aggregator (May 1, 2026 snapshot): SX was better on 56 of 64 match-winner outcomes. Polymarket's 3% taker-fee formula means trades that "look profitable on screen" are not after fees, and the post argues the fee "largely removes" Polymarket from 50–200 bps arbs. Code and data are published. — [SX Bet blog](https://blog.sx.bet/developers/builds/sports-prediction-market-aggregator/)
- Vendor claims (Claw Arbs, May 7, 2026; sells arb software; internally inconsistent): SX "may not have caught up for 30 to 90 seconds" after Polymarket moves. It recommends polling SX every 200–500 ms and estimates "5 to 12" arbs per day with a worked net edge of about 1.5–1.6%. — [Claw Arbs SX Bet arbitrage](https://clawarbs.com/blog/sx-bet-arbitrage/)
- OddsPapi argues Polymarket has 0% overround while sportsbooks carry 3–5% vig, which creates cross-market arbs. It markets a Polymarket-vs-local-bookmaker arb workflow. — [OddsPapi: Polymarket arbitrage vs local bookmakers](https://oddspapi.io/blog/polymarket-arbitrage-local-bookmakers/)
- A 2024 analysis says Azuro's AMM tends to lag SX's order book, because the AMM "only updates when trades occur". This conflicts with the docs, where odds are oracle-pushed. — [Gate Learn analysis](https://www.gate.com/learn/articles/in-depth-analysis-of-polymarket-sx-bet-pred-x-and-azuro-prediction-markets/3936)
- A guide titled "Decentralized Sports Betting Arbitrage … between Polymarket and Limitless" exists. Not fetched, so its content is unverified. — [coinfrontier.guide](https://coinfrontier.guide/articles/sports-betting-arbitrage)
- GitHub arbitrage bots found are Polymarket-centric (e.g. roswelly/polymarket-trading-bot and tufstraka/polybot, which targets a 2% minimum discrepancy to cover fees). None for Azuro, Overtime or SX were surfaced. — [ecosyste.ms roswelly](https://awesome.ecosyste.ms/projects/github.com%2Froswelly%2Fpolymarket-trading-bot); [tufstraka/polybot](https://github.com/tufstraka/polybot)
- Execution risks raised by sources: different settlement rules and timelines across venues, e.g. a weather-abandoned match resolving differently. — [search-surfaced NewsPoly article](https://www.newspoly.net/blog/polymarket-arbitrage)

### Inferences
- The most promising structural edges for the user's scanner:
  - **SX Bet spreads and totals and Polymarket moneylines**, where Polymarket coverage is thin. Note that Venezuela is a restricted country for SX.
  - **Oracle-fed books (Azuro, Overtime) vs Polymarket** during fast moves. Overtime is ruled out for bots by its API policy, and Azuro's bookmaker.xyz bans bots.
  - **Limitless vs Polymarket on soccer.** This may shrink as Limitless aggregates Polymarket liquidity.
- Given Polymarket's sports taker fee (about 0.75% at 50/50) and SX's fee on profit, realistic arb thresholds should sit above about 1–1.5% gross. This matches the vendor worked examples, which are not independent evidence.

### Gaps
- A search summary reported an analyst tracking 47 Azuro-vs-SX events in April 2026 with an average 4.2% odds discrepancy, but I could not attribute it to a specific verifiable page. Treat it as unverified.
- No independent, measured study of latency or lag between Polymarket and any crypto sportsbook was found.
- No Dune or DefiLlama dashboard tracking cross-venue sports price gaps was found.
