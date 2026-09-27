"""TLS between the gateway and the models on the machines: the machine CA and its certificates."""

#: A model behind a machine's model-TLS proxy listens on the loopback address
#: at its published port plus this: 8010 -> 28010. Above Ray's default worker
#: port range (10002-19999), below the ephemeral range (32768-).
INTERNAL_PORT_OFFSET = 20000

#: What an agent reports when it can run the proxy (``static_capabilities``).
CAPABILITY = "model_tls"


def internal_port(public_port: int) -> int:
    """Where a model behind the proxy listens on the loopback address, for *public_port*."""
    port = int(public_port) + INTERNAL_PORT_OFFSET
    if port >= 32768:
        raise ValueError(f"port {public_port} is too high to put behind the model-TLS proxy")
    return port


def supported(capabilities: dict | None) -> bool:
    """Whether a machine's agent can put the model-TLS proxy in front of what it serves."""
    return bool((capabilities or {}).get(CAPABILITY))
