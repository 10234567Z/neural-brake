# Source and model audit

This product audit records the latest paginated history import and chronological model result. It is not a proof that the explorer omitted no chain logs, model effectiveness, production readiness, or a deployed cross-chain circuit breaker.

## Selected source

- Network: Base Sepolia, chain ID `84532`.
- Protocol: Uniswap v3, official Base Sepolia factory `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24`.
- Pool: USDC/WETH 0.3% (`fee=3000`), `0x46880b404CD35c165EDdefF7421019F8dD25F4Ad`.
- Factory lookup: `getPool(USDC, WETH, 3000)` returned that pool on Base Sepolia.
- Token order: token0 Circle Base Sepolia USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (6 decimals); token1 WETH `0x4200000000000000000000000000000000000006` (18 decimals).
- Event ABI: Uniswap v3 `Swap(address indexed sender,address indexed recipient,int256 amount0,int256 amount1,uint160 sqrtPriceX96,uint128 liquidity,int24 tick)`. Topic0 is `0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67`; three topics and five data words.

These identities were checked against the [Uniswap deployment feed](https://developers.uniswap.org/deployments.json), [Circle's token list](https://developers.circle.com/stablecoins/usdc-contract-addresses), direct contract calls and the [Uniswap v3 event interface](https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/interfaces/pool/IUniswapV3PoolEvents.sol).

## Observed data and limits

On 2026-11-11, the [pool log index](https://base-sepolia.blockscout.com/api/v2/addresses/0x46880b404CD35c165EDdefF7421019F8dD25F4Ad/logs) was paginated to exhaustion: 356 pages at 100 log items per page. It returned 28,727 Swap logs across 28,617 transactions and 20,399 blocks, from `2024-02-14T19:54:44Z` to `2026-11-11T16:33:05Z`. The configured RPC verified every transaction receipt and corresponding block header, including exact event bytes, successful receipt status, block hash and timestamp. For 357 explorer items with an empty transaction hash, the transaction was recovered only after matching the same block/log index/event bytes through RPC; those receipts were then verified normally. The result was written atomically to a fresh local SQLite database and exported as JSONL with SHA-256 `7e59e30941e45c9bc889e3b276e7d652990c6eec83e8361c3e9e867c0dcc06ff`.

This establishes that every Swap returned by the exhausted Blockscout pagination was receipt/header verified. It does **not** independently prove that Blockscout indexed every on-chain Swap. The configured RPC's free plan permits only 10 blocks per `eth_getLogs` request, and the public Base Sepolia RPC reports a 200-block limit, so an independent full-range RPC comparison was not performed. An earlier PublicNode scan missed a real Swap at block `46,550,447`, illustrating that a single provider can omit older activity. Finality/reorg coverage is not claimed.

## Offline model evaluation

A reproducible small PyTorch classifier was trained offline on the 28,727-record snapshot. Features use the preceding 10 swaps (log-price return, elapsed time and trade direction); the label is future 10-swap realized volatility above the training-only 90th percentile. Chronological splits were 20,088 train, 4,299 validation and 4,300 test samples. The score threshold was selected on validation at `7,714/10,000`.

The untouched test partition produced TP 77, FP 1,455, FN 85, TN 2,683; precision `0.0503`, recall `0.4753`, F1 `0.0909`, balanced accuracy `0.5618`. It passes the code's minimum gate (at least one TP and balanced accuracy above 0.5), but its 5% precision and high false-positive count are weak evidence and unsuitable for an automated pause decision. The candidate model version is `53059c36681354a9c04a94ca3962eccd3c9d398cbb76206e9a513a4e09ab3050`; dataset SHA-256 is `7e59e30941e45c9bc889e3b276e7d652990c6eec83e8361c3e9e867c0dcc06ff`. Artifact/manifest exist only under `/tmp`; they are not committed or loaded by the API. This test split has now been observed; do not tune against it. A revised model needs a fresh independent holdout.

## Reactive path

Current Reactive documentation lists Base Sepolia (84532) and Reactive Lasna (5318007) as supported development networks and documents the Reactive System subscription and callback-proxy authentication model. This establishes documented API shapes only. No project registry, Reactive guard or vault is deployed; no publication, callback, vault pause or production protection is claimed.
