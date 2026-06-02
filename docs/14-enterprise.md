# 14 — Enterprise: Mandanten, RBAC, Audit, Observability, Deployment

Hebt Laura vom Single-Player-Desktop zum **mandantenfähigen, auditierbaren,
betreibbaren** Produkt. **Additiv & nicht-brechend**: ohne API-Key ist der Principal
der implizite lokale **Owner** (Desktop-Verhalten unverändert).

## Identität & RBAC

- **Organisationen / Users / Memberships** + **API-Keys** (Migration `0002_enterprise.sql`).
- **Auth**: `Authorization: Bearer <key>` → gescopter Principal (org + Rolle). Kein Key →
  lokaler Owner (Desktop). Keys werden nur als **sha256** gespeichert, der Klartext **einmalig**
  bei Erstellung zurückgegeben.
- **Rollen → Permissions** (`auth/permissions.py`):

| Rolle | Permissions |
|---|---|
| `owner` / `admin` | `*` (alles) |
| `editor` | project:write, asset:write, analysis:run, timeline:edit, export:create, read |
| `exporter` | export:create, read |
| `reviewer` | read |

- **Durchsetzung**: `require_permission("…")` als Dependency je schreibendem Endpoint
  (z. B. `POST /projects` → `project:write`, `POST /timelines/{id}/exports` → `export:create`).
  Lokaler Owner besteht alles; gescopte Keys werden geprüft.

### Admin-API

| Endpoint | Permission | Zweck |
|---|---|---|
| `POST /admin/orgs` | admin:manage | Organisation anlegen |
| `POST /admin/orgs/{id}/users` | admin:manage | User + Membership (Rolle) |
| `POST /admin/orgs/{id}/keys` | admin:manage | API-Key erzeugen (Klartext einmalig) |
| `DELETE /admin/keys/{id}` | admin:manage | Key widerrufen |
| `GET /admin/audit` | audit:read | Audit-Log lesen |

## Audit-Log

Append-only `audit_events`: jede sicherheitsrelevante Mutation protokolliert
`principal_kind/id`, `action`, `entity`, `org_id`, Zeit. Aktuell verdrahtet:
`org.create`, `user.add`, `key.create`, `key.revoke`, `project.create`, `export.create`.
Erweiterbar über `audit.record(...)`.

## Observability

- **Prometheus** unter `GET /metrics`: `laura_http_requests_total{method,status}`,
  `laura_http_request_seconds{method}` (Histogram), `laura_jobs_total{kind,status}`.
- HTTP-Middleware misst jede Anfrage; der Job-Runner zählt Job-Ausgänge.
- **OpenTelemetry** (FastAPI/Celery-Instrumentierung) ist der nächste Schritt für Tracing.
- `/metrics` netzwerkseitig beschränken (nur internes Scrape-Netz).

## Deployment (On-Prem / Server)

- **Container**: `services/local-api/Dockerfile` (Python 3.11 + ffmpeg + uv, `laura-api`).
- **Stack**: `deploy/docker-compose.yml` — API + **PostgreSQL** + **Redis** + **Qdrant** + **MinIO**.
  Minimal: nur `api` hochfahren (SQLite-Volume).
- **Server-Backends** (Extra `server`: `psycopg`, `celery[redis]`, `qdrant-client`):
  - **Postgres** statt SQLite — das Schema ist bereits PG-kompatibel; SQLite-spezifische
    Stellen (`BEGIN IMMEDIATE`, `json_object`) im DB-Layer hinter eine Backend-Auswahl ziehen.
  - **Celery + Redis** statt DB-Job-Runner — gleiche Job-Semantik (ADR-0003), Queue-Routing
    an CPU/GPU-Worker.
  - **Qdrant** für semantische Suche; **MinIO/S3** für Objekt-Storage (Signed URLs).
- Env: siehe `.env.example` (`DATABASE_URL`, `REDIS_URL`, `QDRANT_URL`, `S3_*`).

## Sicherheit (Server)

- TLS-Terminierung über Reverse Proxy; API bindet im Container an `0.0.0.0`.
- **RLS** in Postgres pro `org_id`; **Signed URLs** für Objektzugriff statt roher Credentials.
- Kurze Token-TTL, Device-Linking, Key-Rotation/-Revocation (vorhanden).
- Secrets niemals im Repo; Desktop nutzt `safeStorage` (docs/09-security.md).

## Status

| Bereich | Stand |
|---|---|
| RBAC + API-Keys + Audit | ✅ implementiert & getestet (5 Tests) |
| Prometheus-Metriken | ✅ `/metrics` + Middleware + Job-Counter |
| Docker / Compose / `.env` | ✅ Config vorhanden |
| Postgres/Celery/Qdrant-Backends | 🟡 Infra + Extras + Doku; Code-Integration als nächster Schritt |
| OpenTelemetry-Tracing | 🟡 geplant |
