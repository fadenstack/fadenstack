# Edge certificate

nginx serves the console, the API and the OpenAI-compatible gateway over
HTTPS as soon as this directory holds:

| File             | What it is                                              |
|------------------|---------------------------------------------------------|
| `edge.crt`       | the server certificate, PEM                             |
| `edge.key`       | its private key, PEM (keep it `0600`)                   |
| `edge-chain.pem` | the issuer's intermediates, PEM, when there are any     |
| `ca.crt`         | the install's own authority, when `llmport tls generate` made the certificate: what browsers, API clients and agents are told to trust |

Put them here with the CLI, which checks them and restarts the proxy:

```bash
llmport tls generate              # a certificate made here for this machine
llmport tls use CERT KEY          # your own
llmport tls off                   # back to plain HTTP
```

or upload them through the console's TLS admin API, which writes them here
and restarts the proxy itself. Copied in by hand, they are served after
`docker restart llm-port-nginx`. See `docs/installing.md`, "Enabling HTTPS".

The authority's key is kept in `../tls-ca/`, outside every container.
The certificate files are ignored by git; only this note is tracked.
