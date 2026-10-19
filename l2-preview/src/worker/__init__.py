"""BTC-K GPU worker package.

Keep the resident client stdlib-only. Legacy components remain available through
lazy exports, without importing pricing/tokenomics or miner controls at startup.
"""
from importlib import import_module

_EXPORTS = {
    'HardwareProbe': 'hardware_probe', 'GPUTier': 'hardware_probe', 'GPUProfile': 'hardware_probe',
    'TieredPricingEngine': 'pricing_engine', 'ModelTier': 'pricing_engine', 'ModelVariant': 'pricing_engine',
    'ProcessPreemptor': 'process_preemptor', 'MinerState': 'process_preemptor',
    'SymbioticAIWorker': 'symbiotic_worker',
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module('.'+_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
