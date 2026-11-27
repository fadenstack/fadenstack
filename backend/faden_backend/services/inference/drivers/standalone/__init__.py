"""The ``standalone`` driver: one machine, one native engine per deployment, no Ray.

Built for a Mac running vLLM-Metal from a native runtime bundle (``bundles.py``
``NativeRuntimeSpec``), but nothing here is Mac-specific beyond what the bundle
and the agent's capabilities say. The command set it sends is the contract in
``fadenstack-dev/apple-silicon-serving/tracks/CONTRACTS.md`` (§ C1).
"""

from faden_backend.services.inference.drivers.standalone.driver import StandaloneDriver

__all__ = ["StandaloneDriver"]
