# Installing LLM.Port

LLM.Port runs as a set of containers on one Linux server. Its command-line
tool, `llmport`, installs it, upgrades it and backs it up. The machines with
GPUs join afterwards, each through a one-line install from the console.

---

## What you need

- **A Linux server, x86_64.** The published images are x86_64 only. The server
  needs no GPU; the models run on the machines you add. At least 4 cores,
  6 GB of memory (8 GB with the PII, MCP and skills modules) and 60 GB of disk;
  what each part needs is in [System requirements](system-requirements.md).
- **Docker Engine 24 or later with Compose v2**, and your user in the
  `docker` group.
- **Python 3.12 or later**, with `pipx` or `uv` to install the CLI.
- **Ports 80 and 443 free.** The console, the API and the OpenAI-compatible
  gateway are all served on them: plain HTTP on 80, and HTTPS on 443 once
  you [enable it](#enabling-https). To use other ports, see
  [Changing the ports](#changing-the-ports).

## Install

```bash
pipx install llmport-cli        # or: uv tool install llmport-cli
llmport deploy
```

`llmport deploy`:

1. checks Docker;
2. writes the deployment files into `~/llm-port` (or the directory you pass:
   `llmport deploy /opt/llm-port`);
3. generates `.env` there, with a random password for every service;
4. asks whether to serve HTTPS -- with a certificate it makes for this
   machine, or with yours -- and checks that its ports are free (see
   [Enabling HTTPS](#enabling-https) and [Changing the ports](#changing-the-ports));
5. pulls the images for the CLI's own version, for example
   `ghcr.io/llm-port/backend:0.3.0`;
6. starts everything and creates the first admin. It asks for the admin's
   email and a password (leave the password blank to have one generated),
   then shows both;
7. pulls the **runtime images**: the containers the machines run models in,
   `ghcr.io/llm-port/ray-runtime-x86_64` and `ray-runtime-gb10` (NVIDIA DGX
   Spark). Machines download theirs from this server, not from the internet,
   so the server keeps one per kind of machine -- about 10 GB each. Only
   x86_64 machines? `llmport deploy --runtime-images x86_64`; the choice is
   remembered for upgrades. `llmport runtime-images --check` shows what the
   server holds.

With `llmport deploy -y` nothing is asked. The admin is `admin@localhost`
with a generated password, and both are saved in
`~/llm-port/.bootstrap-credentials`. Store them somewhere safe, then delete
that file.

Open `http://<server>` in a browser and sign in.

### Where things are

| | |
|---|---|
| `~/llm-port/` | the deployment: compose file, `.env`, service configuration |
| `~/llm-port/.env` | every password and setting; keep it private |
| `~/llm-port/backups/` | backups (see below) |
| `~/.config/llmport/llmport.yaml` | the CLI's own settings: where the install is, which modules are on |

The data lives in Docker volumes named `llm_port_shared_*` (for example
`llm_port_shared_pg_data`). `llmport down` keeps them; only
`llmport down --volumes` removes them.

## Add the machines that run the models

In the console, open **Machines → Add a machine** and run the line it shows
on each GPU machine. The machine asks to join, and you approve it on the
same page. See [Onboarding a node](onboarding-a-node.md).

The line installs the node agent from the server itself, or from the
project's GitHub releases when the server has no copy. For machines without
internet access, put the agent builds in `~/llm-port/agent-binaries/`
(`llmport-agent-linux-x86_64`, `llmport-agent-linux-aarch64`, from the
[releases page](https://github.com/llm-port/llm-port-core/releases)), and
replace them with the new release's builds whenever you upgrade -- see
[Update the machines' agents](upgrading.md#update-the-machines-agents).

## Upgrade

```bash
pipx upgrade llmport-cli        # or: uv tool upgrade llmport-cli
llmport upgrade
```

The upgrade backs up every database, replaces the deployment files with the
new release's, pulls the new images and restarts the services. Your `.env`
is kept as it is; a release only adds settings it introduces. Changes you
made to the deployment files themselves (`nginx/nginx.conf`, for example)
are replaced, so keep your changes in `.env`.

An upgrade only moves forward: the databases are migrated to the new
release. To go back, restore the backup the upgrade made. See
[Upgrading LLM.Port](upgrading.md).

## Back up and restore

```bash
llmport backup                          # into ~/llm-port/backups/<time>/
llmport restore ~/llm-port/backups/<time>
```

A backup holds a dump of every database, the `.env`, and the migration
versions. Keep copies off the server: a backup on the same disk doesn't
survive losing the disk.

`llmport deploy` offers a nightly backup (at 03:00, keeping the last 7), and
turns it on with `-y`. To change or stop it:

```bash
llmport backup schedule --at 01:30 --retain 14
llmport backup schedule --off
```

It runs as a systemd user timer (`systemctl --user list-timers`), or from
your crontab where there is no systemd. The output goes to
`~/llm-port/backups/backup.log`.

If the server itself is lost, install a new one, restore the backup onto it,
and re-run the install line on each machine. Clusters that were created
after the backup keep running on the machines, and the new server can take
them over without restarting any model. See
[Taking over clusters your machines still run](taking-over-clusters.md).

## Changing the ports

The console, the API and the gateway (`/v1`) are served on port 80, and on
443 once HTTPS is on. Both are published even while HTTPS is off, so both
must be free. `llmport deploy` asks for another port when one is taken, or
takes them up front:

```bash
llmport deploy --http-port 8080 --https-port 8443
```

On an install that is running:

```bash
llmport ports                          # show them
llmport ports --http 8080 --https 8443 # change them; the proxy moves at once
```

They are `LLM_PORT_HTTP_PORT` and `LLM_PORT_HTTPS_PORT` in `~/llm-port/.env`.

## Enabling HTTPS

The console, the API and the gateway are served over HTTPS as soon as the
install holds a certificate; plain HTTP then only redirects to it (and
answers the proxy's own health check).

What is protected and what is not, how the machines come to trust the server,
where the keys are and a hardening checklist: [HTTPS and trust](https-and-trust.md).

`llmport deploy` serves HTTPS by default, with a certificate made here. It
asks when it runs interactively (`generate` is the default answer), and takes
the choice up front:

```bash
llmport deploy --tls generate                 # a certificate made here (the default)
llmport deploy --tls own --tls-cert fullchain.pem --tls-key key.pem
llmport deploy --tls off                      # plain HTTP; remembered, so a later deploy -y keeps it
```

On an install that is running:

```bash
llmport tls status                            # on or off, and the certificate
llmport tls generate [--name console.example.org]
llmport tls use fullchain.pem key.pem [--chain intermediates.pem] [--ca root.pem]
llmport tls off                               # back to plain HTTP
```

Switching between HTTP and HTTPS restarts the proxy (the only container it
touches); a new certificate while HTTPS stays on reloads it instead, so open
connections stay up. Either way the CLI waits until the console answers.

**A certificate made here** (`generate`) names `localhost`, the server's
host names and every address it has, plus any `--name` you add. It is signed
by a certificate authority the CLI makes for this install on first use:
`~/llm-port/tls/ca.crt` is what everyone trusts, and its key stays in
`~/llm-port/tls-ca/`, which no container sees. Import `ca.crt` into the
browsers the console is used from (or accept their warning), and hand it to
API clients (`curl --cacert ca.crt`, `SSL_CERT_FILE=ca.crt`). A new
certificate later -- another address, a renewal -- is signed by the same
authority, so nothing has to be trusted again; `llmport tls off` keeps it too.

**Your own certificate** (`use`) is checked before it is installed: the key
must belong to it (and have no passphrase), and it must be valid today. The
certificate file may carry its intermediates after it. A certificate that
names none of the server's own names or addresses is installed with a
warning, since the console may be reached by a name only DNS knows.
From a **private issuer** (a company CA), give its root with `--ca root.pem`
(or `llmport deploy --tls-ca`); a root at the end of the certificate file is
taken as well. The machines are handed that root like the install's own
authority, so they trust the certificate without anyone copying files to
them.

### Renewing

Nothing is left to remember: once HTTPS is on, `llmport tls renew` runs every
morning (a systemd user timer, `llmport-tls-renew.timer`, or a crontab line)
and changes nothing until something is due.

- **A certificate made here** lasts 825 days and is issued again 30 days
  before it runs out -- or as soon as the server has an address it does not
  list. It keeps every name it had and comes from the same authority, so
  neither the browsers nor the machines notice.
- **Your own certificate** is taken again from the files you gave
  `llmport tls use` whenever they change there. Point it at the files your
  renewal tool rewrites in place (certbot's
  `/etc/letsencrypt/live/<name>/fullchain.pem` and `privkey.pem`, acme.sh,
  your company's tooling) and a renewal is served the next morning. Renewed
  files that fail the checks are not taken; the old certificate stays.
- **The authority itself** lasts ten years. When it is replaced, the old one
  stays published beside the new one while it is valid, and the machines
  fetch both before anything the new one signed is served.

`llmport tls status` shows until when the certificate is valid and how it is
renewed; `llmport doctor` and the console's dashboard warn before it runs out
(the dashboard 30 days before, or 21 when it renews by itself -- then only a
failing renewal gets that far). `llmport tls renew --force` renews now. The
daily run exits with an error when a certificate is close to its end and
nothing here can renew it, so `systemctl --user --failed` shows it too; its
output is in `~/llm-port/logs/tls-renew.log`.

An administrator in root mode can do the same through the console's API:
`POST /api/admin/tls/upload` with the files, then `POST /api/admin/tls/activate`,
which writes them to `~/llm-port/tls/` and restarts the proxy itself
(`DELETE /api/admin/tls` goes back). Whichever wrote last is what is served.

`LLM_PORT_HSTS_MAX_AGE` in `~/llm-port/.env` (seconds, default 0) turns on
HTTP Strict Transport Security once you are sure HTTPS stays; 0 tells
browsers to forget it again. The redirect from plain HTTP is temporary
(307), so browsers do not remember it after HTTPS is switched off.

**The machines follow by themselves** (agent 0.1.14 or later). `llmport
deploy` and `llmport upgrade` make the install's certificate authority even
while HTTPS is off, and every agent fetches it from the server while it is
connected (and again every minute). When HTTPS is switched on, an agent on
the plain-HTTP address is redirected, checks the HTTPS address against the
authority it holds, and moves there; it remembers the move across restarts.
Nothing has to be done on the machines. `llmport-agent show` says when an
agent has moved.

An agent never moves back by itself: plain HTTP after HTTPS would be a
downgrade anyone between the machine and the server could force by blocking
a port. After `llmport tls off`, point each machine back and restart it:

```bash
llmport-agent configure --set BACKEND_URL=http://<server>
systemctl --user restart llmport-agent   # or: sudo systemctl restart llmport-agent
```

**Machines you add** while HTTPS is on get an install line that fetches the
authority first and checks it against the fingerprint the console shows, then
fetches the installer verified against it and hands it to the agent
(`--ca-file`). With a certificate from a public issuer the line is the usual
one.

**Agents before 0.1.14** do not follow. Re-run the install line on the machine
(that updates the agent), or point it at HTTPS by hand with the authority
copied over:

```bash
llmport-agent configure --set BACKEND_URL=https://<server>
# 0.1.13 and earlier do not take TLS_CA_BUNDLE there: add
# LLM_PORT_NODE_AGENT_TLS_CA_BUNDLE=/path/to/ca.crt to the environment file
# llmport-agent show names, then restart the agent.
```

## Installing from source

To build the images yourself, for example to run changes of your own, deploy
from a checkout instead:

```bash
git clone https://github.com/llm-port/llm-port-core.git
cd llm-port-core
pipx install ./llm_port_cli
llmport deploy --build
```

An install from a checkout builds its images on every `llmport upgrade`.
Pull new code first (`git pull`).
