"""Native (non-container) model runtimes: vLLM-Metal on Apple Silicon.

The backend's standalone driver (C1) stages a relocatable Python runtime from
an OCI carrier image and runs ``vllm serve`` from it as a process tree under
the agent. Nothing here runs as root and nothing here is used on Linux.
"""

from faden_agent.native.common import NativeError, NativePaths

__all__ = ["NativeError", "NativePaths"]
