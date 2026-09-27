# HTTPS and trust

What Fadenstack protects with TLS, how the server, the browsers and the machines
come to trust each other, where the keys live, and what is not covered. Every
step below was run on a real installation on 27 September 2026 -- an Ubuntu VM
running the server, two DGX Spark machines running a model -- and the images
show what each step printed. The commands are the operator's own; nothing was
edited on the machines by hand except where this page says so.

For the everyday how-to, see [Installing: Enabling HTTPS](installing.md#enabling-https).
For a map of every connection in an install, and which of them should move to
TLS next, see [Connections and TLS](connections-and-tls.md).

- [At a glance](#at-a-glance)
- [What is protected, and what is not](#what-is-protected-and-what-is-not)
- [The trust model](#the-trust-model)
- [Switching HTTPS on](#switching-https-on)
- [The machines follow by themselves](#the-machines-follow-by-themselves)
- [Commands are signed](#commands-are-signed)
- [Adding a machine while HTTPS is on](#adding-a-machine-while-https-is-on)
- [Your own certificate, and a company CA](#your-own-certificate-and-a-company-ca)
- [Renewal, rotation and warnings](#renewal-rotation-and-warnings)
- [Where the keys are](#where-the-keys-are)
- [Switching HTTPS off](#switching-https-off)
- [Known limitations](#known-limitations)
- [Hardening checklist](#hardening-checklist)
- [Verify it yourself](#verify-it-yourself)

## At a glance

| | |
|---|---|
| **Where TLS ends** | nginx, the one front door: the console, the API (`/api`), the OpenAI-compatible gateway (`/v1`), their WebSockets, and everything the machines' agents do with the server. |
| **Protocols** | TLS 1.3 and 1.2 only; TLS 1.1 and older are refused by the server. HTTP/2. |
| **Certificates** | Made on the server by its own certificate authority (`llmport tls generate`), or yours -- from a public issuer or a company CA (`llmport tls use`). |
| **Machines** | Agents 0.1.14 and later learn the server's authority while connected and move to HTTPS by themselves when it is switched on. They never move back by themselves. |
| **Commands** | Every command the server sends a machine is signed (HMAC-SHA256, keyed by the machine's credential); agents 0.1.15 and later refuse one without a valid signature. |
| **New machines** | The install line pins the server's authority by its SHA-256 fingerprint; a mismatch stops it before anything is downloaded or run. |
| **Renewal** | Daily and automatic. Certificates made on the server are issued again 30 days before they run out; yours are taken again from the files your tooling renews. Renewing does not interrupt the machines. |
| **Warnings** | `llmport tls status`, `llmport doctor`, and the console's dashboard before a certificate runs out. |
| **Keys** | The authority's key never enters a container. Backups carry the certificate material; keys stay readable by their owner only. |
| **Default** | Plain HTTP until you switch HTTPS on: one command, at deploy (`llmport deploy --tls generate`) or later (`llmport tls generate`). |

**Switch HTTPS on.** Without it, everything between the browsers, the API
clients, the machines and the server -- sign-ins, API keys, prompts, the
machines' credentials -- crosses the network in the clear. The signatures on
commands keep anyone from sending a machine one of their own, but they hide
nothing.

## What is protected, and what is not

With HTTPS on:

| Traffic | Protected | Notes |
|---|---|---|
| Browser to console, `/api` | Yes | Session cookie `HttpOnly; SameSite=lax; Secure`. |
| API clients to `/v1` (chat, embeddings, streaming) | Yes | A POST to the plain-HTTP port is redirected with `307`, which keeps the method and the body. |
| Agents to the server: control stream (WebSocket), enrollment, credential rotation | Yes | Verified against the server's authority or the public roots; never falls back to plain HTTP. |
| Agents downloading models and runtime images from the server | Yes | Same connection and verification as the stream. |
| Grafana through the console (`/grafana/`) | Yes | The dashboard's embedded panel goes through nginx. |
| Grafana's own port `3001`, Langfuse `3002` | Not on the network | Plain HTTP, published on `127.0.0.1` only by default; the console reaches Grafana at `/grafana/`. `GRAFANA_BIND` / `LANGFUSE_BIND` publish them. |
| The gateway to the models on the machines (Ray Serve, vLLM) | **No** | Plain HTTP on the machines' network. |
| Ray between machines (cluster, object transfer) | **No** | On the machines' network, as Ray does it. |
| Agents pushing logs to Loki (`3100`) | **No** | Plain HTTP, when Loki is published beyond the server. |
| Between the containers on the server | **No** | Docker's internal network. |

By default everything on the server except nginx (80/443) is published on
`127.0.0.1` only: Grafana, Langfuse, Postgres, Redis, RabbitMQ, ClickHouse,
MinIO, Prometheus, Loki, the gateway's own port. `PROM_BIND` and
`LOKI_BIND`, `GRAFANA_BIND` and `LANGFUSE_BIND` in `.env` can publish those;
they are plain HTTP.

nginx sets `X-Forwarded-For` and `X-Forwarded-Proto` itself, from the
connection it sees, whatever the client sent: the addresses in the audit log,
the rate limits and a machine's recorded address are the real ones.

## The trust model

Three parties have to trust the server: **browsers**, **API clients** and the
**agents** on the machines. What they trust is one file the CLI publishes,
`tls/ca.crt`:

- the install's own **certificate authority** (made by `llmport deploy` or
  `llmport upgrade`, even while HTTPS is off);
- the authority **before a rotation**, while it is still valid;
- a **company CA's root**, when you serve a certificate it issued.

| What | Made by | Key | Lifetime |
|---|---|---|---|
| Install CA | `llmport deploy` / `upgrade`, on first use | EC P-256, `tls-ca/ca.key`, `0600`, never in a container | 10 years, `CA:TRUE, pathlen:0`, `keyCertSign` |
| Server certificate (made here) | `llmport tls generate`, `llmport tls renew` | EC P-256, `tls/edge.key`, `0600` | 825 days, `serverAuth`, names: `localhost`, the host names and every address outside Docker's bridges, plus `--name` |
| Your certificate | you | yours, checked before use | yours |

**Browsers and API clients** are told to trust `tls/ca.crt` once: import it
into the browser, pass it to `curl --cacert`, or set `SSL_CERT_FILE`. A
certificate from a public issuer needs nothing.

**Agents** trust the public roots, a bundle you configure (`TLS_CA_BUNDLE`),
and what they fetched from the server (`server-ca.pem` beside their state). One
TLS context holds all three and is shared by the agent's HTTP client and its
stream, so a new authority counts for the next connection of either.

**How an agent learns the authority.** Over its existing, authenticated
connection: `GET /api/node-files/server-trust` with its machine credential,
when it connects and every minute after. The server offers it before HTTPS is
switched on, so the machines hold it by the time they need it. The CA reaches a
machine only as securely as that connection: a machine enrolled and running
over plain HTTP learns it over plain HTTP. Add machines after HTTPS is on, or
with the pinned install line, to close that gap.

## Switching HTTPS on

Before: plain HTTP. The server's authority already exists -- the upgrade made
it -- and the machines have fetched it.

![HTTPS off, the ports](images/https/01-https-off.png)

![A machine holds the server's authority before HTTPS is on](images/https/02-machine-holds-the-authority.png)

One command makes a certificate for the server, restarts nginx, and schedules
the daily renewal:

![llmport tls generate](images/https/03-tls-generate.png)

From then on, plain HTTP answers only the proxy's own health check and
redirects everything else with `307 Temporary Redirect` (not `301`: browsers
keep a `301` for good and would keep going to HTTPS after it is switched off).
The HSTS header is sent with `max-age=0` until you set `LLM_PORT_HSTS_MAX_AGE`,
so switching HTTPS off again cannot lock browsers out.

Seen from another machine on the network -- without the authority the
certificate is refused, with it everything verifies, and TLS 1.1 is refused by
the server:

![The console seen from another machine](images/https/06-seen-from-another-machine.png)

The session cookie over HTTPS:

```text
set-cookie: fapiauth=<redacted>; HttpOnly; Path=/; SameSite=lax; Secure
```

`Secure` is added whenever the request came over HTTPS, so the same install
still signs in over plain HTTP while HTTPS is off.

## The machines follow by themselves

Nobody touched the machines. When the plain-HTTP address redirected, each agent
checked the HTTPS address against what it trusts, moved there, and remembered
the move:

![The machine's log: moved to HTTPS](images/https/04-machine-follows.png)

![Both machines healthy over HTTPS](images/https/05-machines-over-https.png)

The rules an agent follows:

- Only a redirect from `http://host` to `https://` **on the same host** is
  followed; a redirect anywhere else is ignored and logged.
- The new address has to answer over a connection verified against what the
  agent trusts. A certificate it cannot verify leaves it where it is, with the
  reason in its log.
- The move is kept for the address it was configured with. Setting another
  address by hand (`llmport-agent configure --set BACKEND_URL=...`) forgets it.
- An agent on HTTPS **never falls back to plain HTTP** by itself. A fallback
  would be a downgrade anyone between the machine and the server could force by
  blocking one port.

Agents before 0.1.14 do not follow; update them (re-running the install line
updates an agent) or point them at HTTPS by hand.

## Commands are signed

Every command the server sends a machine -- start a container, join a Ray
cluster, download a model -- carries an HMAC-SHA256 signature over the whole
command, its issue time included. The key is derived from the credential the
machine's agent connected with, on both sides; the server stores only a hash
of that credential and derives the key from the one on the connection, so a
copy of its database signs nothing.

- The agent announces it checks when it connects; the server then signs every
  command on that connection and says so.
- From then on the agent refuses a command without a valid signature
  (`signature_missing`, `signature_invalid`), on every later connection too:
  stripping the signatures off stops working.
- A command older than five minutes is refused as well, unless the server
  re-sends one still running; the agent never runs the same command twice.
- `LLM_PORT_NODE_AGENT_REQUIRE_SIGNED_COMMANDS=true` requires signatures from
  the first connection; `false` switches the check off, for a server rolled
  back to one that does not sign.

Both machines on the test run: after updating to 0.1.15, each logged "The
backend signs its commands; unsigned ones are refused from now on", and
commands sent to them afterwards ran and succeeded.

## Adding a machine while HTTPS is on

A machine being added does not trust the server's authority yet. The console's
install line fetches the authority first and checks it against the fingerprint
the console shows; only then does it trust anything from the server:

![Add a machine: the pinned install line](images/https/07-add-a-machine.png)

```bash
curl -fsSLk -o server-ca.crt https://<server>/api/install/ca.crt &&
echo "<sha256>  server-ca.crt" | sha256sum -c --quiet &&
curl -fsSLO --cacert server-ca.crt https://<server>/api/install/llmport-agent.sh &&
sudo sh llmport-agent.sh --join https://<server> --ca-file server-ca.crt
```

- `-k` is used for the authority's file alone, and that file is checked against
  the fingerprint before it is used. Everything after -- the installer, the
  install plan, the agent download from the server -- is verified against it.
  The agent release from GitHub is verified against the public roots, and its
  digest against what the server says it must be.
- The agent keeps the authority beside its settings (`TLS_CA_BUNDLE`).

A fingerprint that does not match stops the line before anything else is
fetched or run:

![A wrong fingerprint stops the line](images/https/08-wrong-fingerprint.png)

The same line on a machine that is already a member updates its agent:

![The pinned install line on a machine](images/https/09-pinned-install.png)

The fingerprint is only as trustworthy as the console page it was copied from.
Open the console with the authority trusted in the browser, not by clicking
through a certificate warning.

## Your own certificate, and a company CA

`llmport tls use CERT KEY` checks a certificate before it is served: the key
belongs to it and has no passphrase, it is valid today, a full-chain file is
split into the certificate and its chain. A certificate that names none of the
server's own names or addresses is installed with a warning.

From a **company CA**, give its root with `--ca`. It is published to the
machines with the install's own authority, and the certificate must chain to
it. When the machines have a new issuer to learn, the CLI waits a minute before
the switch, so connected machines fetch it first:

![A certificate from a company CA](images/https/11-company-ca.png)

![The machine learnt the company root before the switch](images/https/12-machine-learns-the-issuer.png)

A certificate from a public issuer needs no `--ca`, and the install line stays
the plain one.

## Renewal, rotation and warnings

`llmport tls renew` runs every morning (a systemd user timer; cron where there
is no systemd) and changes nothing until something is due:

- **Made here:** issued again 30 days before it runs out, or as soon as the
  server has an address it does not list. Same authority, same names: browsers
  and machines notice nothing.
- **Yours:** taken again from the files `llmport tls use` was given, whenever
  they change there. Point it at the files your tooling renews in place --
  certbot's `/etc/letsencrypt/live/<name>/fullchain.pem` and `privkey.pem`,
  acme.sh, a company agent. Renewed files that fail the checks are not taken.
- **The authority:** replaced before it runs out; the old one stays published
  while it is valid, so nothing it signed goes untrusted.

![Status, the daily run and the timers](images/https/10-status-and-renewal.png)

A new certificate while HTTPS stays on reloads nginx instead of restarting it:
new connections get the new certificate, and the machines' open streams stay up.
Across the renewal below, the machine logged no reconnect at all.

![Renewal from the company CA's files](images/https/14-renewal-from-source.png)

The dashboard warns 30 days before a certificate runs out -- 21 when it renews
by itself, which only a failing renewal reaches -- and turns critical in the
last week. `llmport doctor` checks it too, and a daily run that cannot renew
anything exits with an error (`systemctl --user --failed`), its output in
`~/llm-port/logs/tls-renew.log`.

![The dashboard: the certificate runs out in 19 days](images/https/13-dashboard-warning.png)

## Where the keys are

| File | Mode | Who reads it | What it is |
|---|---|---|---|
| `tls-ca/ca.key` | `0600`, directory `0700` | the CLI on the host only | the install CA's key |
| `tls-ca/ca.crt`, `ca-previous.crt`, `roots.pem` | `0644` | the CLI | the authority, the one before a rotation, company roots |
| `tls-ca/source.json` | `0600` | the CLI | where your certificate was imported from |
| `tls/edge.key` | `0600` | nginx (read-only mount), the backend container (read-write mount, for the TLS admin API) | the served certificate's key |
| `tls/edge.crt`, `edge-chain.pem` | `0644` | nginx, the backend | the served certificate and its chain |
| `tls/ca.crt` | `0644` | the backend, served to machines and at `/api/install/ca.crt` | what everyone trusts; public |
| `tls/edge.json` | `0644` | the backend | the host's addresses and ports, for the install line |

On a machine: `~/.local/share/llmport-agent/server-ca.pem` (what it fetched),
`server-address.json` (its move to HTTPS), and
`~/.config/llmport-agent/llmport-agent-server-ca.crt` when it was installed
with `--ca-file`.

Certificates uploaded through the console's TLS admin API are stored encrypted
with the server's settings master key; the API needs a superuser in root mode
and every call is audited.

**Backups** carry the certificate material, checksummed in the manifest, so a
server rebuilt from a backup is one the machines still trust. Keep backups as
private as `.env`: they hold private keys.

![A backup holds the certificate material](images/https/15-backup.png)

## Switching HTTPS off

`llmport tls off` removes the served certificate and restarts nginx; the
authority is kept for next time, and the daily renewal stops:

![llmport tls off](images/https/16-tls-off.png)

Machines that followed the server to HTTPS stay there -- by design, see above.
Point each back and restart it:

![A machine after HTTPS was switched off](images/https/17-machine-after-off.png)

## Known limitations

- **Agents before 0.1.15 get unsigned commands.** They changed their signing
  key mid-connection on every credential rotation, so the server signs only
  for agents that announce they check. On plain HTTP, anyone who can intercept
  an older agent's stream can send it commands, containers included; update
  every agent (re-run the install line).
- **Traffic to and between the machines' models is plain HTTP**: the gateway to
  Ray Serve and vLLM, Ray's own traffic, and log pushes to Loki. Keep the
  machines on a network only they and the server share.
- **Grafana has anonymous read access.** Anyone who reaches it -- through the
  console's `/grafana/`, or its own port if you publish it -- sees every
  dashboard.
- **Trust on first use.** A machine enrolled over plain HTTP learns the
  server's authority over plain HTTP. After HTTPS is on, the pinned install
  line closes this for new machines.
- **The backend container can read the served key**, because the TLS admin API
  writes it. The authority's key is never in a container.
- **No revocation.** The install CA publishes no CRL or OCSP. To withdraw it,
  move `tls-ca/` away and generate again: machines then have to be pointed at
  the server again, like after `tls off`.
- **The backend and gateway trust forwarded headers from any address**
  (`FORWARDED_ALLOW_IPS=*`). nginx replaces whatever a client sends, and their
  own ports are not published beyond the server, so only a container on its
  Docker network could set them.
- **HSTS is off** (`max-age=0`) until you choose, so HTTPS can be switched off
  again without locking browsers out.
- **No built-in ACME.** Use certbot or acme.sh and hand the files to
  `llmport tls use`; the daily run picks up every renewal.
- **Agents before 0.1.14** neither learn the authority nor follow; before
  0.1.15 they do not check signatures.

## Hardening checklist

1. `llmport tls generate` (or `llmport tls use` with your certificate) before
   adding machines.
2. Import `tls/ca.crt` into the browsers the console is used from, instead of
   accepting the warning -- the install line's fingerprint is only as good as
   the page it came from.
3. Once HTTPS stays, set HSTS in `~/llm-port/.env` --
   `LLM_PORT_HSTS_MAX_AGE=31536000` -- and apply it with `llmport up nginx`
   (a restart alone keeps the old setting).
4. Leave Grafana and Langfuse on the server (the default): don't set
   `GRAFANA_BIND` or `LANGFUSE_BIND` to `0.0.0.0`. Grafana is in the console at
   `/grafana/`; reach Langfuse through an SSH tunnel
   (`ssh -L 3002:localhost:3002 <server>`).
5. Keep the machines, and the network between them and the server, private:
   model traffic is plain HTTP.
6. Update every machine's agent to 0.1.15 or later (re-run the install line):
   0.1.14 follows the server to HTTPS, 0.1.15 refuses unsigned commands.
7. Keep the nightly backup, and keep backups private: they hold the keys.
8. Watch the dashboard's attention list and `systemctl --user --failed` for the
   renewal.

## Verify it yourself

```bash
llmport tls status                     # served certificate, until when, how it renews
llmport doctor | grep HTTPS
systemctl --user list-timers | grep llmport-tls-renew

# from another machine, with the server's authority
curl -sI http://<server>/v1/models                          # 307 to https
curl --cacert ca.crt https://<server>/api/health            # ok
curl https://<server>/api/health                            # refused without it
openssl s_client -brief -connect <server>:443 -tls1_1       # alert protocol version
openssl s_client -brief -connect <server>:443 -CAfile ca.crt </dev/null | head -3

# on a machine
llmport-agent show                     # moved to HTTPS? which authority it trusts
journalctl --user -u llmport-agent | grep -E "moved|Stored|Connecting"
```
