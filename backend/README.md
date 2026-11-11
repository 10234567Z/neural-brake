# Neural Brake backend (read-only / fail-closed)

This is a local prototype, not an end-to-end Neural Brake deployment. The selected Base Sepolia pool and token metadata below are **provisional inputs**; the backend does not claim that the pool/deployments are integrated, currently active, or proof of model quality. A reproducible offline model trainer is included, but no artifact is bundled or loaded by default. Publication always returns `503`; there is deliberately no signer or transaction-writing code.

## Setup

Requires Python 3.13+. From this directory install with `python -m pip install -e .`, then run `uvicorn neural_brake.api:app`. The API creates a local SQLite cache at `./neural_brake.sqlite3` by default. Do not expose this prototype publicly; operator authentication is not implemented.

Configure using these application environment names (secrets are not stored in this project):

| Variable | Required/use | Default |
|---|---|---|
| `NB_RPC_URL` | Optional for serving; required to enable the explicit read-only source adapter. Use an operator-controlled Base Sepolia JSON-RPC URL. | unset |
| `NB_BLOCKSCOUT_API_KEY` | Optional for historical import when the key's plan supports Base Sepolia. Uses Blockscout's authenticated multichain API instead of the public explorer endpoint. Store only in the runtime environment/ignored local `.env`. | unset |
| `NB_DATABASE_PATH` | Local SQLite file path. | `./neural_brake.sqlite3` |
| `NB_CHAIN_ID` | Expected RPC chain identity. | `84532` |
| `NB_POOL_ADDRESS` | Pool queried by the source adapter. | provisional `0x46880b404CD35c165EDdefF7421019F8dD25F4Ad` |
| `NB_TOKEN0_ADDRESS`, `NB_TOKEN1_ADDRESS` | Provisional pool token references for configuration display. | USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e`, WETH `0x4200000000000000000000000000000000000006` |
| `NB_TOKEN0_DECIMALS`, `NB_TOKEN1_DECIMALS` | Provisional verified token decimals used by the offline price feature calculation. | USDC `6`, WETH `18` |
| `NB_POOL_FEE` | Pool fee tier. | `3000` |
| `NB_MODEL_ARTIFACT` | Future model artifact location; configuring a path does not load or validate a model. | unset |
| `NB_SCORE_REGISTRY_ADDRESS`, `NB_REACTIVE_ADDRESS`, `NB_VAULT_ADDRESS` | Future deployment references; configured references are not verified deployments. | unset |

RPC URL and Blockscout API key values may contain credentials and must be provided through the runtime environment/secret manager, never committed. No signer/private-key environment variable is defined because publishing is disabled. See [official API authorization and route docs](https://docs.blockscout.com/devs/blockscout-api-responses-and-routes). The free Blockscout API key returned HTTP 402 for Base Sepolia in this environment; the authenticated multichain endpoint requires a Builder, Business, or Pro plan for this chain. Do not upgrade without explicit budget approval. Without a supported key, unset `NB_BLOCKSCOUT_API_KEY` and use the public Base Sepolia explorer endpoint.

## API and data

Implements the proposed `GET /api/v1/health/live`, `/health/ready`, `/config`, `/swaps`, `/candidates/latest`, and `/candidates/{candidateId}` routes, plus a fail-closed `POST /candidates/{candidateId}/publish` and `/audit`. Readiness currently returns 503 because source/model/deployments are not validated and there is no loaded model. Empty candidates mean unavailable—not zero risk or a computed score. Swap pagination is capped at 100; cursors are bounded opaque offsets. `/audit` reports only what is actually present in the local cache, without a go/no-go threshold.

`neural_brake.ingest.RpcSource` provides a bounded (`<= 2001` blocks), read-only JSON-RPC log query and strict Uniswap v3 Swap event decoder. It verifies RPC chain ID, pool address, event signature, and the two canonical indexed address topics; rejects removed logs and malformed/noncanonical ABI fields. Ingestion fetches each distinct referenced block, requires the returned block number/hash to match the log, and stores that block's actual UTC timestamp. The bounded range is validated before any returned records are persisted. Run `neural-brake-ingest --from-block N --to-block M` to explicitly query and cache a bounded range; it does not run as a worker and does not assert finality, completeness, or current live activity. The stored observations are a cache, not chain authority.

For a bounded historical audit, `neural-brake-verify-history --since 2026-09-18T00:00:00Z` paginates the selected Base Sepolia pool's Blockscout event index down to the requested UTC cutoff, then verifies each included Swap byte-for-byte against a successful transaction receipt and its timestamp/block hash against the configured Base Sepolia RPC before persisting. Set `NB_BLOCKSCOUT_API_KEY` to use the authenticated multichain API; without it the command uses the public Base Sepolia explorer. The command rate-limits page reads, retries bounded transient transport failures, uses bounded JSON-RPC batches, and aborts without adding rows if pagination, receipt, ABI, block hash or timestamp validation fails. This is an explicit audit/import, not a live worker. The RPC must retain historical receipts for the requested pool; this procedure does not claim finality or a successful Reactive callback. A full-history run was still in progress when the local execution limit was reached; no complete import has been established yet.

Export successfully verified cached records with `neural-brake-export-swaps --output /path/to/source.jsonl`. The exporter filters to the configured chain and pool, requires actual timestamps, and emits a data hash. The export path is created only when explicitly requested; the repository does not seed or ship product observations.

## Offline model pipeline

Training uses a real JSONL snapshot of normalized Swap observations, never test fixtures. Unit fixtures for feature-window behavior remain isolated under `backend/tests/`. Install the optional CPU PyTorch dependency with `python -m pip install -e '.[model]' --extra-index-url https://download.pytorch.org/whl/cpu`, then run:

```sh
python -m pip install -e '.[model]' --extra-index-url https://download.pytorch.org/whl/cpu
neural-brake-train --dataset /path/to/rpc-verified-swaps.jsonl --output /path/to/neural-brake.pt
```

The trainer validates chain, pool, token pair, timestamps and unique log identities; builds log-price returns from Uniswap `sqrtPriceX96` with the verified USDC/WETH decimals; uses a chronological 70/15/15 split, a future realized-volatility label cutoff derived only from training data, and a score threshold selected on validation. It writes weights plus a manifest containing the data hash, time bounds, split counts, label rule, reproducible weights/model hashes and validation/test metrics. It refuses to train if a split lacks both classes, and refuses an operational artifact if the chronological test holdout fails its minimum gate (balanced accuracy above chance and at least one detected positive). `--allow-research-artifact` explicitly permits a failed artifact for offline analysis only; it is marked `failed-research-only` and must not be loaded for live scoring. The current experiment failed this gate (test F1 `0.0`; see `docs/data-audit.md`). No trained artifact is configured or loaded in the API.

Audit the current cache with `neural-brake-audit`; it reports actual count and timestamp span and configuration presence only. There are no product seed rows, mock data, training fixtures, scores, or synthetic events.
