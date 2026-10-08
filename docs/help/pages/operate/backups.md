# Backups

`faden backup` saves everything the server needs to come back: every database, the `.env` with its keys, and the
certificate material the machines trust. `faden restore` puts it back; restoring is still being tested (see [Restore](#restore)). A nightly backup is set up
when you install.

## What a backup holds

| Part | In the backup | Why it matters |
|---|---|---|
| Every database on the server's Postgres | Yes | Users, settings, providers, machines, clusters, deployments, chats, the request log |
| `.env` | Yes, unless `--db-only` | It holds the key the chats are encrypted with and the keys that seal stored secrets (the Hugging Face token, secret settings, the MCP servers' credentials). A database restored without its `.env` has chats and secrets nobody can read |
| Certificate material (`tls/`, `tls-ca/`) | Yes, unless `--db-only` | The served certificate and the server's own certificate authority with its private key. With them, a rebuilt server is one the machines still trust |
| Migration versions and checksums | Yes | `faden restore` checks the database dumps and the certificate files before it changes anything |
| Files uploaded to the knowledge base or attached to chats | No | The text read from them is in the database; the files themselves are not. Keep the originals you need |
| Models | No | They can be downloaded again. Keep a copy of models you imported rather than downloaded |
| Logs, metrics, traces | No | They are kept on the server for viewing, not restored |
| Runtime images | No | `faden runtime-images` fetches them again |
| The GPU machines | Nothing to back up | They hold no state the server needs; their clusters keep running if the server is lost |

!!! warning
    A backup holds private keys and every secret of the install. Keep backups as private as `.env`, and keep
    copies off the server: a backup on the same disk does not survive losing the disk.

## Back up now

```bash
faden backup
```

It asks for confirmation (`-y` skips it), then writes `~/fadenstack/backups/<time>/` and lists what it saved.

| Option | What it does |
|---|---|
| `--output-dir PATH` | Write the backup elsewhere, such as a mounted backup disk. A relative path is taken inside the install directory |
| `--retain N` | Keep only the N most recent backups in that directory (default 5). Older ones there are deleted |
| `--db-only` | Databases only, without `.env` and certificate material. Keep a copy of `.env` apart, as private as the backups |
| `-y` | Do not ask |

??? info "How `--retain` counts"
    `--retain` counts every backup in the directory, including the nightly ones and the ones upgrades take. A
    manual `faden backup` into the default directory keeps 5 unless you pass a larger number, and every
    `faden upgrade` keeps 5.

## The nightly backup

`faden deploy` offers a nightly backup at 03:00 that keeps the last 7, and turns it on with `-y`. To change or
stop it:

```bash
faden backup schedule --at 01:30 --retain 14
faden backup schedule --off
```

Its output goes to `~/fadenstack/backups/backup.log`. Read that log now and then: a backup that fails at night
says so only there.

??? info "How the nightly backup is started"
    It runs as a systemd user timer (`systemctl --user list-timers`), or from your crontab where there is no
    systemd.

The nightly backup writes into `~/fadenstack/backups/`, on the same disk as the data. Copy the backups elsewhere
with your own tooling, or schedule `faden backup -y --output-dir <your backup disk>` with your own scheduler
instead.

Every `faden upgrade` also takes a backup before it changes anything.

## Restore

!!! warning "Under testing"
    Restoring is being tested. It puts back the data: the databases, the `.env`, the certificate material, and
    volumes if the backup holds them. It does not put back the deployment files or the release the backup was
    taken with. While it runs, the services are stopped and the console does not answer: there is no
    maintenance page yet. Try a restore on a copy of the server before you rely on one.

```bash
faden restore ~/fadenstack/backups/<time>
```

It asks for confirmation (`-y` skips it).

??? info "What the restore does, in order"
    1. shows the backup's date, the tool version it was taken with, and what it holds;
    2. checks the database dumps and the certificate files against the checksums in the backup, and stops if one
       does not match;
    3. asks for confirmation (`-y` skips it);
    4. stops the services, restores every database and, unless told otherwise, the `.env` and the certificate
       material;
    5. starts everything again.

| Option | What it does |
|---|---|
| `--verify-only` | Check the backup's checksums, restore nothing |
| `--db-only` | Restore the databases only |
| `--skip-env` | Keep the current `.env` |
| `-y` | Do not ask |

!!! warning
    A restore replaces the current databases. Everything done since the backup is lost: chats, users, settings,
    machines approved since.

### Check a backup

Run `faden restore --verify-only` on a copy of a backup now and then. It tells you whether the database dumps and the
certificate files are complete and unchanged, without touching the install.

## Rebuild a lost server

Restoring is still being tested: try it on a copy of the server first (see [Restore](#restore)).

1. Install a new server with the same release the backup was taken with (`faden restore --verify-only` shows it),
   using `faden deploy`.
2. Copy the backup onto it and run `faden restore` with its path.
3. If the new server has another address, run the install line from its console on each machine again, so the
   agents connect to it (see [GPU machines](machines.md)).
4. Run `faden runtime-images` if the machines will need runtime images the server does not hold yet.
5. Clusters created after the backup are not in it, but their machines still run them. Take them over instead of
   creating them again: see [Taking over clusters](taking-over.md).

Because the backup carries the certificate authority, machines that trusted the old server trust the new one.
