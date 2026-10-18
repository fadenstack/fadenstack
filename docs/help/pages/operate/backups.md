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
| Migration versions and checksums | Yes | `faden restore` checks the database dumps, the volume archives and the certificate files before it changes anything |
| Files uploaded to the knowledge base | With `--include-volumes` | Without it, only the text read from them is in the backup, not the files themselves. With `FADEN_DATA_DIR` set, the backup reads (and a restore writes) its `knowledge` folder |
| Files attached to chats | With `--include-volumes` | Without it, only the text read from them is in the database, not the files themselves. With `FADEN_DATA_DIR` set, the backup reads (and a restore writes) its `chat-files` folder |
| Models | No | They can be downloaded again. Keep a copy of models you imported rather than downloaded |
| Logs, metrics, traces | With `--include-volumes`, except metrics | They are kept on the server for viewing; a backup without them loses nothing the server needs |
| Runtime images | No | `faden runtime-images` fetches them again |
| The GPU machines | Nothing to back up | They hold no state the server needs; their clusters keep running if the server is lost |

!!! warning
    A backup holds private keys and every secret of the install. Encrypt them (see
    [Encrypted backups](#encrypted-backups)), keep them as private as `.env`, and keep copies off the server: a
    backup on the same disk does not survive losing the disk.

On the server, a backup is readable by the user who runs `faden` only, like `.env`; `faden deploy` and
`faden upgrade` do the same for backups made before.

## Back up now

```bash
faden backup
```

It asks for confirmation (`-y` skips it), then writes `~/fadenstack/backups/<time>/` and lists what it saved.

| Option | What it does |
|---|---|
| `--output-dir PATH` | Write the backup elsewhere, such as a mounted backup disk. A relative path is taken inside the install directory |
| `--retain N` | Keep only the N most recent backups of this kind in that directory (default 5). Older ones of the same kind are deleted |
| `--db-only` | Databases only, without `.env` and certificate material. Keep a copy of `.env` apart, as private as the backups |
| `--include-volumes` | Also the files uploaded to the knowledge base, the files attached to chats, the logs, the traces and Grafana's own data. Not the models |
| `-y` | Do not ask |

??? info "How `--retain` counts"
    Each backup records what made it: the nightly timer, you with `faden backup`, or `faden upgrade`. `--retain`
    counts and deletes only backups of its own kind, so a manual `faden backup --retain 3` leaves the nightly
    backups alone, and an upgrade does not push them out. A manual backup keeps 5 unless you pass another number,
    the nightly one keeps 7 (see below), and every `faden upgrade` keeps 5 of its own. Backups made before this
    release have no kind and count as manual; a nightly timer set up before it keeps counting as manual until you
    run `faden backup schedule` again.

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

## Encrypted backups

Once the install has a backup key, every backup is encrypted. Each file in it is encrypted to the key's public
half, which stays on the server, so the nightly backup runs on its own. Only the **recovery key** opens a backup:
without it nobody reads one, whether they hold the backup disk or administer this server. Fadenstack keeps no copy
of it.

`faden deploy` makes the key. `faden upgrade`, run by someone at the terminal, offers to make it once;
`faden upgrade -y` leaves the backups as they were. To make it yourself:

```bash
faden backup key create
```

1. The recovery key is shown once: `FADEN-BACKUP-KEY1` and twelve groups of characters.
2. Keep a copy off the server, in a password manager or on paper kept somewhere safe, and confirm that you did.
3. The screen is cleared, and you are asked for two groups of the key, at random positions, from your copy. Three
   wrong answers show the key again from the start.

With `--write-to PATH` the key goes into a file instead, such as one on a USB stick (never inside the install
folder), and is read back to check it.

??? info "After an unattended install (`faden deploy -y`)"
    Nobody is there to see the key, so it is left in `~/fadenstack/backup-recovery-key.txt`, readable by the user
    who runs `faden` only. While it is there, the key is on the same server as the backups: `faden backup`,
    `faden upgrade` and `faden backup key status` remind you until you copy it off the server and run
    `faden backup key confirm`. That asks for two groups from your copy, then deletes the file.

| Command | What it does |
|---|---|
| `faden backup key status` | Whether backups are encrypted, to which key, and what is still to do |
| `faden backup key check` | Checks that your copy of the recovery key is still this install's; nothing is stored. Do it once a year and with every restore test |
| `faden backup key create --replace` | A new key. The backups made so far still need the old recovery key |

Only a small `backup.json` stays readable in an encrypted backup: when it was made, what made it, and which key
opens it. Backups made before the install had a key stay as they are and are restored as before; delete them once
encrypted ones exist.

!!! warning
    Without the recovery key, the backups made with it cannot be restored, by anyone.

## Restore

!!! warning "Under testing"
    Restoring is being tested. It puts back the data: the databases, the `.env`, the certificate material, and
    volumes if the backup holds them. It does not put back the deployment files or the release the backup was
    taken with. While it runs, the services are stopped and the console shows the maintenance page (as in an
    upgrade), which reloads itself once the platform is back. Try a restore on a copy of the server before you rely on one.

```bash
faden restore ~/fadenstack/backups/<time>
```

It asks for confirmation (`-y` skips it). An encrypted backup asks for the recovery key first.

??? info "What the restore does, in order"
    1. opens an encrypted backup with the recovery key, into a folder inside it that only you can read and that is
       deleted when the restore ends;
    2. shows the backup's date, the tool version it was taken with, and what it holds;
    3. checks the database dumps, the volume archives and the certificate files against the checksums in the backup, and stops if one
       does not match;
    4. asks for confirmation (`-y` skips it);
    5. puts up the maintenance page, stops every service except the front door (nginx), restores every database
       and, unless told otherwise, the `.env` and the certificate material;
    6. starts everything again and takes the maintenance page down, also when the restore fails.

| Option | What it does |
|---|---|
| `--verify-only` | Check the backup's checksums, restore nothing |
| `--db-only` | Restore the databases only |
| `--skip-env` | Keep the current `.env` |
| `--key-file PATH` | Read the recovery key from this file instead of asking for it |
| `-y` | Do not ask |

!!! warning
    A restore replaces the current databases. Everything done since the backup is lost: chats, users, settings,
    machines approved since.

### Check a backup

Run `faden restore --verify-only` on a copy of a backup now and then. It tells you whether the database dumps, the volume archives and the
certificate files are complete and unchanged, without touching the install.

## Rebuild a lost server

Restoring is still being tested: try it on a copy of the server first (see [Restore](#restore)).

1. Install a new server with the same release the backup was taken with (`faden restore --verify-only` shows it),
   using `faden deploy`.
2. Copy the backup onto it and run `faden restore` with its path, and the recovery key if the backup is
   encrypted. The new server encrypts its own backups to the key its `faden deploy` made.
3. If the new server has another address, run the install line from its console on each machine again, so the
   agents connect to it (see [GPU machines](machines.md)).
4. Run `faden runtime-images` if the machines will need runtime images the server does not hold yet.
5. Clusters created after the backup are not in it, but their machines still run them. Take them over instead of
   creating them again: see [Taking over clusters](taking-over.md).

Because the backup carries the certificate authority, machines that trusted the old server trust the new one.
