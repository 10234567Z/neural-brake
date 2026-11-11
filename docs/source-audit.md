# Source and model audit

This product audit records observed chain data and validation results. It is not a claim of full historical coverage, model effectiveness, production readiness, or a deployed cross-chain circuit breaker.

## Selected source

- Network: Base Sepolia, chain ID `84532`.
- Protocol: Uniswap v3, official Base Sepolia factory `0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24`.
- Pool: USDC/WETH 0.3% (`fee=3000`), `0x46880b404CD35c165EDdefF7421019F8dD25F4Ad`.
- Factory lookup: `getPool(USDC, WETH, 3000)` returned that pool on Base Sepolia.
- Token order: token0 Circle Base Sepolia USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (6 decimals); token1 WETH `0x4200000000000000000000000000000000000006` (18 decimals).
- Event ABI: Uniswap v3 `Swap(address indexed sender,address indexed recipient,int256 amount0,int256 amount1,uint160 sqrtPriceX96,uint128 liquidity,int24 tick)`. Topic0 is `0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67`; three topics and five data words.

These identities were checked against the [Uniswap deployment feed](https://developers.uniswap.org/deployments.json), [Circle's token list](https://developers.circle.com/stablecoins/usdc-contract-addresses), direct contract calls and the [Uniswap v3 event interface](https://github.com/Uniswap/v3-core/blob/v1.0.0/contracts/interfaces/pool/IUniswapV3PoolEvents.sol).

## Observed data and limits

The first 80 pages of the [pool log index](https://base-sepolia.blockscout.com/api/v2/addresses/0x46880b404CD35c165EDdefF7421019F8dD25F4Ad/logs) contained 2,947 Swap logs in 2,941 transactions, from `2026-09-09T00:03:16Z` through an explorer-reported `2026-11-10T12:46:00Z`; the API reported another page, so this is not a full count. The latest reported item was not independently verified.

A read-only `eth_getLogs` scan through block `56,964,027` in 233 ranges returned 2,410 logs across 2,406 transactions and 2,252 blocks. Every returned log passed the Uniswap ABI checks and its block hash matched an `eth_getBlockByNumber` response from `base-sepolia-rpc.publicnode.com`. The observed range was `2026-09-18T23:46:54Z` to `2026-11-10T10:28:16Z`; median interval was 32 seconds, maximum 65,376 seconds. This snapshot is temporary and is not complete lifetime history.

The RPC provider omitted an earlier pool log: the official Base Sepolia RPC independently returned a real Swap at block `46,550,447`, log index 23, transaction `0xc97a6a8f0bd5497e145d2834272a29529251f97b5ba18665e7dcbb60ae7a1314`, block hash `0x1b6e09a5fa4a819f24373a8cafd25034d75776c233ad22e848bdbcf744982cbf`, timestamp `2026-09-08T12:06:22Z`. The Blockscout and RPC snapshots also differed on some event keys. Complete history and indexer reconciliation remain unresolved. A historical receipt import attempt did not complete, and it persisted no rows.

## Offline model evaluation

A reproducible exploratory model was trained on the 2,410-observation temporary snapshot. Features used 10 previous swaps (log-price return, elapsed time and trade direction); labels used future 10-swap realized volatility, with a 90th-percentile cutoff from training only. The split was chronological 70/15/15: 1,667 train, 351 validation and 352 test samples. A score threshold of 8,838/10,000 was selected on validation.

On the untouched test partition, 11 samples were positive. Results were TP 0, FP 112, FN 11, TN 229, F1 0.0 and balanced accuracy 0.336. The held-out result fails to establish useful generalization. Repeated runs produced the same weights hash and model version `b575ab3f13e70815af9ba27ba7a483746e43c1491361a2722286b30cf126b397`. Artifacts were written only under `/tmp`, are not distributed or loaded, and must not be used for live scoring. Do not tune against this test split.

## Reactive path

Current Reactive documentation lists Base Sepolia (84532) and Reactive Lasna (5318007) as supported development networks and documents the Reactive System subscription and callback-proxy authentication model. This establishes documented API shapes only. No project registry, Reactive guard or vault is deployed; no publication, callback, vault pause or production protection is claimed.
