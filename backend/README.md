# Neural Brake backend (read-only / fail-closed)

This is a local prototype, not an end-to-end Neural Brake deployment. The selected Base Sepolia pool and token metadata below are **provisional inputs**; the backend does not claim that the pool/deployments are integrated, currently active, or proof of model quality. A reproducible offline model trainer is included, but no artifact is bundled or loaded by default. Publication always returns `503`; there is deliberately no signer or transaction-writing code.

## Setup

Requires Python 3.13+. From this directory install with `python -m pip install -e .`, then run `uvicorn neural_brake.api:app`. The API creates a local SQLite cache at `./neural_brake.sqlite3` by default. Do not expose this prototype publicly; operator authentication is not implemented.

Configure using these application environment names (secrets are not stored in this project):

| Variable | Required/use | Default |
|---|---|---|
| `NB_RPC_URL` | Optional for serving; required to enable the explicit read-only source adapter. Use an operator-controlled Base Sepolia JSON-RPC URL. | unset |
| `NB_BLOCKSCOUT_API_KEY` | Optional for historical import when the key's plan supports Base Sepolia. Uses Blockscout's authenticated multichain API instead of the public explorer endpoint. Store only in the runtime environment/ignored local `.env`. | unset |
| `NB_LASNA_RPC_URL` | Optional Reactive Lasna read-only RPC for `neural-brake-preflight`. No signer is used. | `https://lasna-rpc.rnk.dev/` |
| `NB_DATABASE_PATH` | Local SQLite file path. | `./neural_brake.sqlite3` |
| `NB_CHAIN_ID` | Expected RPC chain identity. | `84532` |
| `NB_POOL_ADDRESS` | Pool queried by the source adapter. | provisional `0x46880b404CD35c165EDdefF7421019F8dD25F4Ad` |
| `NB_TOKEN0_ADDRESS`, `NB_TOKEN1_ADDRESS` | Provisional pool token references for configuration display. | USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e`, WETH `0x4200000000000000000000000000000000000006` |
| `NB_TOKEN0_DECIMALS`, `NB_TOKEN1_DECIMALS` | Provisional verified token decimals used by the offline price feature calculation. | USDC `6`, WETH `18` |
| `NB_POOL_FEE` | Pool fee tier. | `3000` |
| `NB_MODEL_ARTIFACT` | Optional research model artifact. It is loaded only when `NB_RESEARCH_DATASET` is also set and hashes/source metadata validate. This does not make readiness pass or enable publication. | unset |
| `NB_RESEARCH_DATASET` | Optional receipt-verified JSONL snapshot matching the artifact's training dataset hash. Enables `GET /api/v1/research/latest-score`; scoring remains research-only. | unset |
| `NB_SCORE_REGISTRY_ADDRESS`, `NB_REACTIVE_ADDRESS`, `NB_VAULT_ADDRESS` | Future deployment references; configured references are not verified deployments. | unset |

RPC URL and Blockscout API key values may contain credentials and must be provided through the runtime environment/secret manager, never committed. No signer/private-key environment variable is defined because publishing is disabled. See [official API authorization and route docs](https://docs.blockscout.com/devs/blockscout-api-responses-and-routes). The free Blockscout API key returned HTTP 402 for Base Sepolia in this environment; the authenticated multichain endpoint requires a Builder, Business, or Pro plan for this chain. Do not upgrade without explicit budget approval. Without a supported key, unset `NB_BLOCKSCOUT_API_KEY` and use the public Base Sepolia explorer endpoint.

## API and data

Implements the proposed `GET /api/v1/health/live`, `/health/ready`, `/config`, `/swaps`, `/candidates/latest`, and `/candidates/{candidateId}` routes, plus a fail-closed `POST /candidates/{candidateId}/publish` and `/audit`. Readiness currently returns 503 because source/model/deployments are not validated and there is no loaded model. Empty candidates mean unavailable—not zero risk or a computed score. Swap pagination is capped at 100; cursors are bounded opaque offsets. `/audit` reports only what is actually present in the local cache, without a go/no-go threshold.

When a validated artifact and its exact verified training snapshot are configured, `GET /api/v1/research/latest-score` returns one explicitly `research_only` score for the latest snapshot observations. This endpoint is read-only: `/health/ready` remains 503, `/candidates/latest` remains unavailable for operational use, and publication still returns 503. The latest score is based on the supplied static snapshot, not a live feed.

To try that local-only research endpoint, set all three paths before starting the API:

```sh
export NB_DATABASE_PATH=/path/to/receipt-verified-history.sqlite3
export NB_MODEL_ARTIFACT=/path/to/neural-brake.pt
export NB_RESEARCH_DATASET=/path/to/the-exact-training-snapshot.jsonl
uvicorn neural_brake.api:app
```

The dataset hash must match the model manifest. The score remains research-only; no ingestion worker, transaction signing, publication, or pause is enabled.

Before planning any testnet deployment, run `neural-brake-preflight` with `NB_RPC_URL` set to a Base Sepolia RPC. This read-only check verifies Base Sepolia/Lasna chain identities, code at the current documented factory, pool, callback proxy and System addresses, plus the pool's token order, fee and factory lookup. It does not verify funding, a signer, deployed project contracts, callback delivery, or vault state. The documented network addresses must be rechecked immediately before deployment; never put keys in this command.

`neural_brake.ingest.RpcSource` provides a bounded (`<= 2001` blocks), read-only JSON-RPC log query and strict Uniswap v3 Swap event decoder. It verifies RPC chain ID, pool address, event signature, and the two canonical indexed address topics; rejects removed logs and malformed/noncanonical ABI fields. Ingestion fetches each distinct referenced block, requires its number/hash to match the log, verifies each Swap against a successful transaction receipt with matching event bytes/provenance, and stores the block's actual UTC timestamp. The entire requested range is validated before atomic local persistence. Run `neural-brake-ingest --from-block N --to-block M` to explicitly query and cache a bounded range (the configured RPC's free plan may restrict `eth_getLogs` to 10 blocks per request); it does not run as a worker and does not assert finality, completeness, or current live activity. The stored observations are a cache, not chain authority.

For opt-in read-only **network** monitoring of newer swaps, run `neural-brake-watch --from-block N --max-blocks 100 --follow` after explicitly setting an absolute `NB_DATABASE_PATH` to the operator-approved cache. This process sends only chain-read RPC requests and updates that local SQLite cache: it scans at most 10 blocks per RPC log query, validates returned swaps against successful receipts and block headers, and stays at least 64 blocks behind the reported head. Follow mode waits `--poll-seconds` between bounded cycles and prints `next_from_block`; use that block on restart. Without `--follow`, it scans one bounded cycle; `--to-block N` limits a single cycle. The first block is **operator-chosen**—the watcher cannot establish missing history before it. RPC log queries can omit events, a 64-block lag is not proof of finality, and interrupted multi-range cycles may leave earlier validated ranges in the cache. It does not refresh the model's configured training snapshot, compute a live neural score, publish, or trigger a callback. Do not mistake its local cache for an independently complete chain feed.

The latest full pagination for the selected pool exhausted 356 Blockscout pages and returned 28,727 Swap logs across 28,617 transactions and 20,399 blocks, spanning `2024-02-14T19:54:44Z` to `2026-11-11T16:33:05Z`. Every returned Swap was matched byte-for-byte to a successful RPC receipt and its block hash/timestamp to an RPC header; 357 missing explorer transaction hashes were resolved from same-block RPC logs. The validated dataset was committed atomically to a fresh local database and exported with SHA-256 `7e59e30941e45c9bc889e3b276e7d652990c6eec83e8361c3e9e867c0dcc06ff`. This completes the Blockscout pagination import, but not independent proof that the explorer omitted no on-chain logs or finality/reorg coverage.

Export successfully verified cached records with `neural-brake-export-swaps --output /path/to/source.jsonl`. The exporter filters to the configured chain and pool, requires actual timestamps, and emits a data hash. The export path is created only when explicitly requested; the repository does not seed or ship product observations.

## Offline model pipeline

Training uses a real JSONL snapshot of normalized Swap observations, never test fixtures. Unit fixtures for feature-window behavior remain isolated under `backend/tests/`. Install the optional CPU PyTorch dependency with `python -m pip install -e '.[model]' --extra-index-url https://download.pytorch.org/whl/cpu`, then run:

```sh
python -m pip install -e '.[model]' --extra-index-url https://download.pytorch.org/whl/cpu
neural-brake-train --dataset /path/to/rpc-verified-swaps.jsonl --output /path/to/neural-brake.pt
neural-brake-score --dataset /path/to/rpc-verified-swaps.jsonl --artifact /path/to/neural-brake.pt
```

The trainer validates chain, pool, token pair, timestamps and unique log identities; builds log-price returns from Uniswap `sqrtPriceX96` with the verified USDC/WETH decimals; uses a chronological 70/15/15 split, a future realized-volatility label cutoff derived only from training data, and a score threshold selected on validation. The full-history run produced 20,088 train, 4,299 validation and 4,300 test samples. Test results: F1 `0.0909`, precision `0.0503`, balanced accuracy `0.5618`, with 77 true positives and 1,455 false positives. This passes the trainer's minimum gate but is weak and not suitable for automated pausing; the test partition has been observed and must not be tuned against. `neural-brake-score` and `/api/v1/research/latest-score` produce explicitly `research_only` scores from a verified snapshot; neither publishes nor pauses. No trained artifact is loaded by default. See `docs/source-audit.md` for provenance and limits.

Audit the current cache with `neural-brake-audit`; it reports actual count and timestamp span and configuration presence only. There are no product seed rows, mock data, training fixtures, scores, or synthetic events.
