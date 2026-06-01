# Coolify deployment

## Service configuration

- **Build pack**: Dockerfile (this repo's root `Dockerfile`).
- **Exposed port**: `8000`.
- **Healthcheck**: `GET /healthz` (already declared in the Dockerfile).

## Persistent storage

None required. The database is in-memory only — nothing is written to
disk, and all data is wiped on every restart/redeploy (by design, for
data protection). Do not mount a volume.

## Environment variables

| Name | Required | Example | Notes |
| --- | --- | --- | --- |
| `BASE_URL` | yes | `https://debate.example.org` | Used for share links shown to admins. No trailing slash. |
| `IMPRINT_TEXT` | recommended | `Verein e.V.\nStr. 1\n80331 München\n…` | Free-form Impressum shown on `/legal`. Newlines are preserved. If unset, `/legal` shows a placeholder listing the required fields. |

## After first deploy

1. Open `BASE_URL/healthz` — should return `{"ok": true}`.
2. Open `BASE_URL/` — landing page (once Chunk 6 lands).
3. Create an event; bookmark the admin URL it issues.
