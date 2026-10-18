# Rotating the keys

`faden secrets rotate` replaces the keys that protect the data your server stores, with new random ones, and
re-encrypts everything stored under the old ones. Nothing has to be entered again: the saved tokens, the
providers' API keys, the MCP servers' credentials and the chats stay as they are, and **the GPU machines stay
connected**.

## When to rotate

- **The install runs on a published key.** The installer writes random keys. An install that was started by hand
  before release 0.4.2 may still run on the development keys that are published with the source code, with
  `FADEN_ALLOW_PUBLISHED_KEYS=true` in `.env`, and a warning every time a service starts. Anyone who reads the
  source code knows those keys.
- **`.env` may have leaked**: a copy in a ticket, a backup that left the building.
- **An administrator who could read `.env` has left.**

`faden secrets status` shows which of the keys are published values, and whether a rotation was left unfinished:

```bash
faden secrets status
```

It lists the variables and their state, never the keys themselves.

## What is rotated

| Key in `.env` | What it protects |
|---|---|
| `FADEN_BACKEND_SETTINGS_MASTER_KEY` | The secrets the console stores (the Hugging Face token, the TLS key, secrets of the knowledge base, the tokens of Ray clusters), the API keys of remote providers, and the private keys of the machine certificate authority |
| `FADEN_MCP_ENCRYPTION_KEY` | The headers and environment of MCP servers: bearer tokens and API keys. Only where the MCP module is on |
| `FADEN_PROVENANCE_SIGNING_KEY`, `FADEN_PROVENANCE_HASH_KEYS`, `FADEN_PROVENANCE_WATERMARK_KEYS` | What marks generated content as AI-generated: the key that signs the marks gets a new key and certificate from the install's authority; the hash keys and the text watermark keys get a new version, and every older version stays, so marks made before can still be checked. A model running now keeps its watermark key until it next starts. Only where the provenance module is on |
| `FADEN_GATEWAY_ENCRYPTION_KEY` | The chats: messages, titles, memory and attachment notes. After a rotation of this key, chats stored in plain text are sealed with the new one (see below) |

These are not rotated:

| Not rotated | Why |
|---|---|
| `FADEN_BACKEND_CREDENTIAL_PEPPER` | It keeps the machines' credentials valid. A credential is stored as a hash, and a hash cannot be re-made without the machine's secret, so changing it would disconnect every machine. `faden upgrade` adds it to `.env`, with the value the master key had until then |
| `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY` | Langfuse seals its own data with them and cannot re-encrypt it. `faden secrets status` still says when they are published values |
| Passwords and tokens that seal nothing (`POSTGRES_PASSWORD`, the service tokens) | Nothing stored depends on them. Change them in `.env` and restart; `faden deploy` shows which belong together |

## Chats stored in plain text

Installs from before 2 October 2026 stored their chats unencrypted, and an install without a chat key did too.
`faden upgrade` writes a chat key where none is set and then seals those chats, while the platform is in use.
Run it yourself at any time; it finishes what a run that stopped left:

```bash
faden secrets seal
```

It reports how many values it sealed. A value that looks sealed but opens with no key the install has is
counted and left as it is.

## Rotate

```bash
faden secrets rotate
```

It lists the keys it will rotate and asks for confirmation (`-y` skips it).

| Option | What it does |
|---|---|
| `--only settings`, `--only mcp`, `--only chat` | Rotate just that key. Repeat the option for two. Default: every key the install uses |
| `--resume` | Finish a rotation that stopped. Running the command again does the same |
| `--skip-unreadable` | Leave stored values that neither the new nor the old key opens as they are, instead of stopping |
| `-y` | Do not ask |

What happens, in this order:

1. **A backup**, with the current `.env` in it. Without a backup nothing is changed. The services must be healthy
   first; if one is not, the command says which and stops.
2. **The new keys go into `.env`**, each next to the old one, which stays as `..._PREVIOUS`. From here on the
   services open data stored under either key.
3. **The services that use the keys restart** behind the maintenance page.
4. **Every stored value is encrypted again with the new key**, in one step per database, and checked: a value
   that the new key does not open alone is never saved. The command prints how many values it converted per
   database, never a value.
5. **Only then the old keys are removed from `.env`**, and `FADEN_ALLOW_PUBLISHED_KEYS` with them when no published
   key is left. The services restart once more and the maintenance page goes away.

Expect a few minutes for the restarts. Chats are the large part: the more chats, the longer step 4 takes.

!!! note
    While the command runs, people see the maintenance page. A chat reply that is being written when the services
    restart is lost; the user asks again.

## If it stops

A rotation can stop: the server loses power, a service does not come back, or a value cannot be read. The new keys are
already in `.env` next to the old ones, so the services open everything they did before. Look at the message,
fix what it names, and run the command again:

```bash
faden secrets rotate --resume
```

It continues where it stopped, with the same new keys, and skips values that are already done. `faden secrets
status` shows `rotation unfinished` until it has.

| You see | What to do |
|---|---|
| `These services are not healthy` | Nothing was changed. Fix them (`faden status`, `faden logs`) and run the command again |
| `The backup failed` | Nothing was changed. Free disk space or fix the error it names, then run the command again |
| `open with neither the new nor the previous key` | Some stored value was already unreadable before the rotation, for example one that was saved under a key that is lost. The command stops so that you notice. Delete or re-enter that value, or run `faden secrets rotate --resume --skip-unreadable` to leave it as it is |
| `The services did not restart` or `The backend did not become healthy` | Look at `faden logs backend`, fix the cause, run `faden secrets rotate --resume` |

To go back to before the rotation, restore the backup it took ([Backups](backups.md)): its `.env` holds the old keys.

## After a leak

Rotating replaces the keys, but a key that leaked also opened the data that was stored while it was valid.
Anything that was secret in that data is worth changing at its source: provider API keys, tokens saved in MCP
servers, the Hugging Face token. The old backups still need their old `.env`; keep them as private as before, or delete them.
