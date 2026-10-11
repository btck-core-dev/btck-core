# Security Policy

## Reporting Security Vulnerabilities

The Bitcoin K (BTC-K) development team takes the security and integrity of the BlockDAG network, consensus protocol, and cryptographic wallet architecture seriously.

If you discover a potential vulnerability, bug, or security risk in `btck-core`, please **DO NOT** disclose it publicly via GitHub Issues, Discussions, or public forums. Responsible coordinated disclosure ensures the protection of user funds and node networks.

### Coordinated Disclosure Channel

* **Primary Security Contact**: `security@bitcoin-k.com`
* **Response Time**: We acknowledge receipt of vulnerability reports within 48 hours and provide assessment updates as verification progresses.

When reporting, please include:
1. A clear description of the vulnerability (e.g., consensus fork trigger, memory corruption, RPC vulnerability, stealth key leakage).
2. Steps to reproduce or proof-of-concept (PoC) code in a private/local devnet context.
3. Your assessment of potential impact and severity.
4. (Optional) Your preferred contact method or PGP key for encrypted communication.

---

## Experimental Software & Risk Disclosure

1. **Experimental Stage**: BTC-K is experimental cryptographic software under active decentralized development. It is provided "as is", without warranty of any kind.
2. **Guardian Transition Phase**: Prior to the completion and public binding of consensus checkpoint `#271828`, the network operates in an initial bootstrapping / guardian state.
3. **No Financial Guarantees**: Running a node, mining, or handling assets involves technical, operational, and market risks. Never risk funds or hardware capacity you cannot afford to lose.
4. **Third-Party Audits**: BTC-K has undergone extensive internal test suites, multi-node rehearsals, and automated static/dynamic checks. However, users and miners must be aware that decentralized software evolves, and no cryptographic system can guarantee absolute absence of zero-day vulnerabilities.
5. **Private Key Responsibility**: Neither the developers nor the repository maintainers have access to, nor will they ever request, your private keys, seed phrases, or RPC tokens. Keep your credentials strictly offline.
