# Security and operational limits

This repository is a testnet prototype, **not** a production DeFi safety system. The no-value vault accepts no deposited assets; native testnet gas funding for callback delivery is distinct from deposits. Do not send valuable funds to it.

- A dedicated testnet address is authorized as reporter on the test ScoreRegistry, but its encrypted key remains local. There is no backend signing path or authenticated publication API; the write API remains disabled. Do not enable automated publication until operator authentication, consent, rate limits, idempotency, signer custody, deployment read-back and transaction reconciliation are implemented and reviewed.
- Do not commit or paste private keys, seed phrases, provider tokens or access credentials. The manual test used an encrypted local keystore; no key is stored in the application. Any future automated publication requires a separate authenticated, reviewed signer design. A client-wallet signing redesign requires an explicit product decision.
- On a suspected key compromise: stop the signer, revoke the reporter in the registry through its verified owner, rotate the key, and audit actual chain events. An admin unpause is a separate recovery action; it must never be presented as proof that Reactive paused the vault.
- A model score is an advisory signal, not cryptographic proof of inference. A registry event proves an authorized address published it; only authoritative destination-chain state and callback evidence can establish whether deposits were paused.

One manual, no-value testnet callback was verified with an explicitly labeled test vector; this is not a security audit or production readiness claim. No bug bounty is asserted. Report issues privately to the repository owner using an established private channel once one is available; **do not publish secrets in an issue**.
