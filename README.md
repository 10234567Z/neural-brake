# Neural Brake

Neural Brake is a risk-monitoring product concept that combines real Uniswap v3 pool events, an offline-trained neural risk signal, and a Reactive callback intended to pause deposits to a no-value vault.

Licensed under [MIT](LICENSE).

## Current status

This workspace contains a local read-only API, a responsive dashboard, Solidity contracts with isolated behavior tests, and an offline PyTorch training pipeline. It is **not yet a functioning deployed circuit breaker**:

- The selected source is the Base Sepolia Uniswap v3 USDC/WETH 0.3% pool, `0x46880b404CD35c165EDdefF7421019F8dD25F4Ad`.
- The read API can decode and cache genuine Swap events. A one-event read-path check was verified against Base Sepolia RPC.
- The offline model experiment on a provider-limited snapshot failed its untouched chronological holdout. The model is not loaded; the API reports no candidates.
- The API's publication route returns `503`. No signer, operator approval flow, deployed registry/Reactive/vault contracts, callback transaction, or paused vault exists.
- Contracts and local tests do not demonstrate a live cross-chain integration. The vault accepts no assets and must never be used to protect funds.

See [`docs/source-audit.md`](docs/source-audit.md) for pool provenance, data limits and the failed holdout result; [`contracts/NOTE.md`](contracts/NOTE.md) for deployment-order and integration constraints; and [`SECURITY.md`](SECURITY.md) for operational limits.

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

The trainer uses chronological train/validation/test partitions, derives the volatility-label cutoff from training data only, and chooses a candidate score threshold on validation. It refuses an operational artifact if the heldout set fails the minimum gate. `--allow-research-artifact` is only for an explicitly marked offline experiment and must not be used for live scoring or publication. The current observed model fails the holdout; do not lower its threshold or tune against the test split to force an alert.

## Before enabling any write

1. Obtain a complete, receipt-verified history for the selected pool using an archive-capable provider. The current PublicNode snapshot omitted older real pool activity; see the audit.
2. Establish a model that passes a fresh chronological holdout on complete, real observations. Freeze features, model hash and threshold before a live event.
3. Confirm the precise Reactive callback route, addresses and funding requirements against current official documentation. Deploy registry, Reactive contract and no-value vault only on the supported development networks, with explicit owner approval.
4. Implement operator authentication, approved signer custody, idempotent publication and safe reconciliation before enabling writes. Never place keys in the browser, repository, logs or chat.
5. Independently reconcile the source event, score publication receipt, Reactive callback transaction and authoritative vault state. A dashboard status or local test is not proof of the full loop.

No production safety, model effectiveness, deployment, public repository, or completed cross-chain loop is claimed here.
