# Neural Brake

Neural Brake is a risk-monitoring product concept that combines real Uniswap v3 pool events, an offline-trained neural risk signal, and a Reactive callback intended to pause deposits to a no-value vault.

Licensed under [MIT](LICENSE).

## Current status

This workspace contains a local read-only API, a responsive dashboard, Solidity contracts, and an offline PyTorch training pipeline. A **manual no-value testnet callback path has been exercised**, but this is not an operational model-driven or production circuit breaker:

- The selected source is the Base Sepolia Uniswap v3 USDC/WETH 0.3% pool, `0x46880b404CD35c165EDdefF7421019F8dD25F4Ad`.
- A full Blockscout history pagination run returned 28,727 Swap logs from 2024-02-14 through 2026-11-11. Every returned log was matched to a successful RPC receipt and block header; independent proof that the explorer omitted no logs is still outstanding.
- A separate opt-in observer can scan newer confirmed block ranges, validate returned Swap receipts, and update the local cache. It does not prove RPC completeness or run live neural inference.
- The offline neural model passed only the trainer's minimum holdout gate on that snapshot. Test precision was 5.0% (77 true positives, 1,455 false positives); that is weak evidence, not a model suitable for automated pausing. A local opt-in research endpoint can show a score from the exact snapshot; no operational candidate is approved.
- A one-off manual test vector (`9000`, above the contract threshold) was published by the authorized testnet reporter and the Reactive callback paused the no-value vault. It was explicitly **not** produced by the neural model; the backend publication route still returns `503`.
- The testnet registry, Reactive guard, and vault are deployed and individually verified. The vault accepts no deposited assets and must never be used to protect funds.

See [`docs/source-audit.md`](docs/source-audit.md) for pool provenance, verified history scope and model metrics; [`contracts/NOTE.md`](contracts/NOTE.md) for deployment-order and integration constraints; and [`SECURITY.md`](SECURITY.md) for operational limits.

For the no-value testnet rehearsal and verified manual callback evidence, see [`contracts/DEPLOYMENT.md`](contracts/DEPLOYMENT.md). Future Foundry script steps remain dry-run only unless an operator explicitly passes `--broadcast` and approves that specific transaction.

## Local development

Requirements: Python 3.13+, Node.js 22+, npm, and Foundry.

```sh
# Solidity behavior checks; local only, no network transaction
cd contracts && forge test

# Read-only API and backend checks
cd ../backend
python3 -m pip install -e '.[test]'
python3 -m pytest -q tests

# Dashboard build
cd ../frontend
npm ci
npm run typecheck
npm run build
```

Run the API with an operator-controlled Base Sepolia JSON-RPC URL in `NB_RPC_URL`; the frontend dev server proxies `/api` to `127.0.0.1:8000`. The backend is a local prototype and has no operator authentication; do not expose it publicly.

## Read a verified source event

From `backend/`, configure a read-only Base Sepolia RPC in the local environment. Do not commit an RPC URL containing credentials.

```sh
export NB_RPC_URL='<your Base Sepolia read RPC URL>'
export NB_DATABASE_PATH='./neural_brake.sqlite3'
neural-brake-ingest --from-block 56923137 --to-block 56923137
neural-brake-audit
neural-brake-export-swaps --output /tmp/neural-brake-swaps.jsonl
```

The block contains a previously observed real Swap for the selected pool. This read verifies the RPC network, event identity/ABI, and block hash/timestamp before caching. It is not a live feed, a complete history, a score, finality proof, or callback evidence. The JSONL export contains only records already present in the local cache.

## Model pipeline

Install the optional CPU model package only when training from a source snapshot whose provenance and coverage have been audited:

```sh
cd backend
python3 -m pip install -e '.[model]' --extra-index-url https://download.pytorch.org/whl/cpu
neural-brake-train --dataset /path/to/verified-swaps.jsonl --output /path/to/neural-brake.pt
```

The trainer uses chronological train/validation/test partitions, derives the volatility-label cutoff from training data only, and chooses a candidate score threshold on validation. It refuses an operational artifact if the heldout set fails the minimum gate. `--allow-research-artifact` is only for an explicitly marked offline experiment and must not be used for live scoring or publication. The current candidate passes that minimal gate but has only 5.0% test precision and many false positives; it is not loaded or approved for automated action. Do not tune against its already-observed test split.

## Before enabling any write

1. The full Blockscout pagination was receipt/header verified, but independent source-completeness reconciliation remains open. The configured RPC's free plan restricts `eth_getLogs` ranges; use a suitable independent source before claiming chain-wide completeness.
2. Establish useful model performance—not just the minimum gate—on a fresh, real chronological holdout. The latest test split has already been observed and must not be tuned against. Freeze features, model hash and threshold before a live event.
3. The manual testnet callback route was verified once. Confirm current Reactive callback/funding parameters before any repeated run; deployment and destination configuration require separate explicit approval.
4. The research model is not useful enough for automated action (5.0% holdout precision). Keep publication disabled until a new, independent evaluation supports a future model and operator authentication/key custody are reviewed.
5. The manual test vector and callback have been reconciled, but no real model score has been published and the dashboard does not read on-chain callback state. A dashboard status is not proof of a live AI-driven loop.

The repository is public and one manual testnet callback is verified; no production safety, useful model effectiveness, or completed AI-driven loop is claimed here. The opt-in research score is not a live feed or an instruction to pause deposits.
