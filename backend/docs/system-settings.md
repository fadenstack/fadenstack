# System Settings Control Plane

## Overview

`/api/admin/system/settings` exposes schema-driven configuration with immediate apply semantics.

Core behavior:
- Non-secret settings are persisted in `system_setting_value`.
- Secrets are encrypted at rest in `system_setting_secret`.
- Each setting has an `apply_scope`: `live_reload`, `service_restart`, or `stack_recreate`.

## Security

- Encryption key source: `FADEN_BACKEND_SETTINGS_MASTER_KEY`.
- Secret reads return masked previews only.
- Protected keys require active root mode when updated.
- All setting updates are audited via admin audit log.

## API

- `GET /api/admin/system/settings/schema`
- `GET /api/admin/system/settings/values`
- `PUT /api/admin/system/settings/values/{key}`

## Immediate Apply

- `live_reload`: no apply job.
- `service_restart` / `stack_recreate`: create `system_apply_job` + `system_apply_job_event`.
- Job status endpoint: `GET /api/admin/system/apply/{job_id}`.

## Every process

Each backend process (every uvicorn worker, the taskiq worker) holds the runtime settings in its own
`settings` object. Triggers on `system_setting_value` and `system_setting_secret` move a one-row count,
`system_settings_epoch`, in the change's own transaction (`faden_backend/db/settings_epoch.py`). A process
reads the count before each request, each task and each background pass; when it moved, the process loads
every registered runtime key again, values and secrets, including those extensions register. A missing or
blank stored value gives the key back the value it had before the process first loaded the settings (the
environment's or the code's default). A change therefore reaches every process with its next request,
whichever process saved it and however it was written (`services/system_settings/runtime_sync.py`).
