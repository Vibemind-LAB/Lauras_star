# ADR-0003 — DB-basierter Job-Runner (Desktop), Celery nur im Servermodus

- **Status:** akzeptiert
- **Kontext:** Heavy Jobs (probe/proxy/analysis/export) brauchen eine Queue. FastAPI-Background-Tasks
  sind zu leichtgewichtig. Broker-Pflicht (Redis) auf jedem Schnittrechner ist zu schwer.

## Entscheidung

**Desktop:** DB-basierter lokaler Job-Runner (Claim/Lease/Heartbeat/Reaper über die SQLite-DB).
**Server/On-Prem:** umschaltbar auf **Celery + Redis** mit Queue-Routing. **Gleiche Job-Semantik** in beiden.

## Begründung

- Desktop soll **ohne** zusätzliche Infrastruktur laufen (local-first, weniger Ops).
- Job-Lebenszyklus (`queued→leased→running→succeeded|failed|canceled`) + `attempt`,
  `lease_expires_at`, `heartbeat_at`, `idempotency_key` machen Abstürze/Doppelausführung beherrschbar.
- Celery dokumentiert Queue-Routing sauber für den späteren Servermodus.

## Konsequenzen

- Eine Abstraktion `JobQueue` mit zwei Backends (DB-Runner, Celery) — API-identisch.
- Reaper-Logik für abgelaufene Leases ist Pflicht (Crash-Recovery).
- Modell-Worker (ASR/diarize) als eigene Prozesse mit Modell-Cache.
