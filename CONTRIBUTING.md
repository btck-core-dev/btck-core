# Contributing to Bitcoin K (BTC-K)

Thank you for your interest in contributing to Bitcoin K. As a decentralized, high-throughput PoW BlockDAG project with dual-key stealth address privacy and KHeavyHash ASIC mining, we welcome contributions across the node daemon, stratum protocol, cryptography, and developer tooling.

---

## Development Principles & Security Standards

1. **Deterministic Consensus**: Any modification touching consensus rules, block validation, GHOSTDAG blue selection, or DAA difficulty adjustments must include exhaustive unit and regression tests.
2. **Reproducible Builds**: All release builds are strictly reproducible across isolated clean environments. Avoid introducing host-specific dependencies.
3. **Strict OpSec & Privacy**: Never commit private keys, test wallets with actual funds, testnet seeds, internal IP addresses, or local filesystem credentials. All commits must pass pre-commit secret scanning.
4. **No Premature Feature Creep**: L2 bridges, DEX modules, and external custodial endpoints remain disabled on Layer 1. Contributions should focus on L1 consensus robustness, P2P network reliability, and wallet usability.

---

## Contribution Workflow

1. **Fork & Branch**:
   * Fork the repository and create a feature or bugfix branch (`feature/your-feature-name` or `fix/issue-description`).
   * Keep changes focused and atomic.
2. **Coding Standards**:
   * Adhere to PEP 8 for Python codebase.
   * Document public functions and consensus-critical logic.
   * Preserve existing license headers.
3. **Testing**:
   * Run the test suite locally before opening a pull request:
     ```bash
     pytest tests/
     ```
   * Ensure all tests pass with zero regressions.
4. **Pull Request Submission**:
   * Open a PR with a clear description of the rationale, implementation, and verification steps.
   * Link relevant issues.
   * Maintainers and peer reviewers will review and perform deterministic cross-checks before merging.

---

## Code of Conduct

Contributors are expected to maintain professional, constructive, and respectful communication across all project channels. Harassment, disruptive conduct, or malicious exploit injections will result in immediate disqualification and banning.
