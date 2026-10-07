# System Initialization Wizard

## Purpose

The wizard provides step-based initialization while reusing the same settings update path as General Settings.

## Endpoints

- `GET /api/admin/system/wizard/steps`
- `POST /api/admin/system/wizard/apply`

## Steps

1. Host Target
2. Core Data Services
3. Auth and Secrets
4. LLM Gateway
5. Langfuse / Grafana / Loki
6. PII Protection
7. Health Verification

## Execution Model

- Step apply calls settings updates directly.
- Any non-live setting triggers immediate apply behavior.
- Apply failures are returned per-setting and retained in apply job events. An unknown key, an invalid value
  or a protected key without root mode fails the whole request instead.
