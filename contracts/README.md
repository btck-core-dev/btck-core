# Experimental contracts (not part of the L1 launch)

The contracts in this directory are retained as disabled research artifacts.
They are not included in the BTC-K node, pool, or wallet release archives, and
the approved MAINNET manifest keeps L2, bridge, DEX, and wBTCK features off.

`npm test` runs only the dependency-light arithmetic simulation.
`test_wBTCK.mjs` is a legacy in-memory EVM test and is intentionally excluded
from the default dependency graph: Ganache 7.9.2 currently bundles dependencies
with unresolved security advisories. Do not install or deploy it as part of the
public L1 release. A future L2 security review must select a maintained EVM test
runtime and produce a separate signed release.
