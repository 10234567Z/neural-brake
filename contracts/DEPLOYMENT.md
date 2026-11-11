# Manual no-value testnet integration

This is a **preparation runbook**, not a deployment or approval. `script/DeployTestnet.s.sol` has only been compiled and simulated without `--broadcast`; no project contract has been deployed. Do not use this vault with valuable assets.

## Safety and model status

- The only model candidate has 5.0% chronological holdout precision and its latest snapshot score is `3601/10000`, below its validation-selected threshold `7714`.
- Do not lower the threshold to force a model trigger. The demo deployment must remain manual; no model-driven publication is enabled.
- The `ScoreRegistry` reporter is a testnet write authority. Use a dedicated testnet-only signer and keep its encrypted keystore/password local. Never put a private key, seed phrase, keystore password, or RPC token in this repository, browser, log, or chat.
- No transaction is approved by this document. Obtain explicit operator approval before every broadcast or faucet transfer.

## Local signer and test funds

If a dedicated Foundry keystore is needed, create it in a local terminal. The password prompt is hidden:

```sh
mkdir -p "$HOME/.foundry/keystores"
cast wallet new "$HOME/.foundry/keystores" neural-brake-testnet --password
cast wallet address --account neural-brake-testnet
```

Keep the account name/password local. The derived public address can be used for read-only balance checks and `NB_TESTNET_OWNER`; it is the only wallet detail that may be shared. Do not use a mainnet wallet.

- Get Base Sepolia test ETH from a faucet linked by the [official Base faucet documentation](https://docs.base.org/base-chain/tools/network-faucets).
- Reactive's [official Lasna faucet guide](https://dev.reactive.network/reactive-mainnet#get-testnet-react) describes converting Base Sepolia SepETH to lREACT, currently at `0x2afaFD298b23b62760711756088F75B7409f5967`, with a maximum of 5 SepETH per request. Use only the smallest supported test amount, and do not send anything until you explicitly approve that faucet transaction.
- A callback destination needs Base Sepolia ETH for callback fees; the Reactive contract needs lREACT. Deployment gas and ongoing service fees are separate from the no-value `deposit()` call. Current fee parameters can change; re-read them and simulate before funding.

## Read-only preflight

With `NB_RPC_URL` configured privately for Base Sepolia, run from `backend/`:

```sh
export NB_LASNA_RPC_URL='https://lasna-rpc.rnk.dev/'
neural-brake-preflight
```

This checks latest-block chain IDs, documented contract code, pool tokens/fee and the factory lookup. It does not verify balances, project deployments, event delivery, callback success, or vault state. Repeat immediately before any deployment.

## Deployment order

The scripts require the local public owner address to equal the `--sender`. Do not add `--broadcast` during preparation. First set non-secret configuration in your local shell:

```sh
export NB_TESTNET_OWNER='0x...public-testnet-address...'
export NB_TESTNET_REPORTER="$NB_TESTNET_OWNER" # one-account testnet demo only
export NB_REACTIVE_THRESHOLD=7714
export NB_CALLBACK_GAS_LIMIT=250000 # provisional; not a verified callback gas setting
export NB_LASNA_RPC_URL='https://lasna-rpc.rnk.dev/'
```

Then simulate each step. These examples deliberately omit `--broadcast` and do not send transactions:

```sh
# 1. Base Sepolia: deploy registry; record the simulated address only as a rehearsal.
forge script script/DeployTestnet.s.sol:DeployTestnet --sig 'deployScoreRegistry()' \
  --rpc-url "$NB_RPC_URL" --sender "$NB_TESTNET_OWNER"

# 2. Lasna: set NB_SCORE_REGISTRY_ADDRESS to a real deployed Base registry before broadcasting.
forge script script/DeployTestnet.s.sol:DeployTestnet --sig 'deployReactiveGuard()' \
  --rpc-url "$NB_LASNA_RPC_URL" --sender "$NB_TESTNET_OWNER"

# 3. Base Sepolia: set NB_REACTIVE_GUARD_ADDRESS to the real Lasna deployment.
forge script script/DeployTestnet.s.sol:DeployTestnet --sig 'deployTestVault()' \
  --rpc-url "$NB_RPC_URL" --sender "$NB_TESTNET_OWNER"

# 4. Lasna: set both addresses to the real deployments and rehearse one-time configuration.
forge script script/DeployTestnet.s.sol:DeployTestnet --sig 'configureDestination()' \
  --rpc-url "$NB_LASNA_RPC_URL" --sender "$NB_TESTNET_OWNER"
```

The actual sequence is: deploy the Base registry → deploy the Lasna Reactive guard → deploy the Base no-value vault → configure the vault once on the guard. The guard's destination is immutable after configuration. Before each later step, read back all prior addresses and chain IDs. Do not use the predicted addresses printed by a dry run as deployed addresses.

Only after an explicit separate approval may a step be broadcast, using the encrypted local account (which prompts for its password); never pass `--private-key`:

```sh
forge script script/DeployTestnet.s.sol:DeployTestnet --sig 'deployScoreRegistry()' \
  --rpc-url "$NB_RPC_URL" --account neural-brake-testnet --broadcast
```

Repeat with the appropriate function/RPC for each step only after reviewing its current simulation, destination, constructor values, estimated fees and signer. The script does not fund contracts or publish a score. Callback gas 250,000 is an unverified rehearsal value; the latest Base proxy reported 80,000 extra gas plus 50,000 charge gas, so confirm actual forwarded gas before any callback transaction.

## What is not verified

The no-value vault's local unit test passes, and the dry-run deployment scripts compile. There is no real deployment, funded Reactive contract, reporter publication, callback transaction, or authoritative vault pause. A manual high score would be a test vector, not an AI-generated signal; do not publish one without separately agreeing to that exact test.
