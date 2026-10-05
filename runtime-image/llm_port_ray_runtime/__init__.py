"""LLM.Port Ray Runtime Helper Package.

Exposes CLI utilities and SDK status normalization for containerized
Ray + Ray Serve LLM + vLLM deployments on DGX Spark / GB10.
"""

__version__ = "0.2.0"

#: What this helper can do beyond the 0.1 contract, for the Node Agent to ask
#: before it falls back to doing the same from outside (``faden_agent.ray.scale``).
#: ``serve_scale``: fixed-copy apps are deployed with Ray's external scaler on,
#: and ``scale-serve-app`` changes their copy count without restarting copies.
FEATURES = ("serve_scale",)

