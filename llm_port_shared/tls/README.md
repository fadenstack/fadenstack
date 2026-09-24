# Edge certificate

nginx serves the console, the API and the OpenAI-compatible gateway over
HTTPS as soon as this directory holds:

| File             | What it is                                              |
|------------------|---------------------------------------------------------|
| `edge.crt`       | the server certificate, PEM                             |
| `edge.key`       | its private key, PEM (keep it `0600`)                   |
| `edge-chain.pem` | the issuer's intermediates, PEM, when there are any     |

Copy them in and restart the proxy (`llmport up nginx`), or upload them
through the console's TLS admin API, which writes them here and restarts
the proxy itself. Remove them and restart to go back to plain HTTP.
See `docs/installing.md`, "Enabling HTTPS".

The certificate files are ignored by git; only this note is tracked.
