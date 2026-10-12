# central-system

The control-center (CC) backend for the contamination detection & tracing demo. Owns the
database, the MQTT ingestion pipeline, the tracing engine, and the read/write REST API that
`dashboard/` talks to. This is a fully self-contained repo/service — it does not import
anything from `node/`; the only coupling to `node/` is over MQTT (the wire protocol) and, for
the demo-only simulation control proxy, a narrow HTTP passthrough (see below).

## Stack

FastAPI, Pydantic v2, SQLAlchemy 2.0 (async, `asyncpg`), Alembic, `asyncio-mqtt`, APScheduler,
numpy/pandas for the shared physics helpers used by the tracing engine's mass-balance math.

## Layout

```
app/
  main.py            FastAPI app, lifespan (starts MQTT ingestion + APScheduler)
  config.py           settings from .env
  database.py          async engine/session
  registry.py           loads config/registry.json
  schemas.py            wire (MQTT) + REST Pydantic models
  models/                SQLAlchemy ORM tables
  mqtt_ingestion.py        MQTT subscriber: register/summary/alert/status handlers
  physics.py                 shared hydraulics/advection-dispersion/decay functions (§3 of spec)
  scheduler.py                 APScheduler jobs: retention cleanup, baselining flip, periodic trace scan
  tracing/
    engine.py                    run_tracing / classify_cause / propagate_uncertainty
  api/
    nodes.py, edges.py, incidents.py, models.py, register.py    real REST endpoints
    sim_proxy.py                                                  demo-only proxy, see below
alembic/                 migrations
config/registry.json       duplicated verbatim from node/config/registry.json
docker-compose.yml           Postgres 16 + Mosquitto for local dev
mosquitto.conf
```

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit if you changed default ports/credentials

docker-compose up -d   # Postgres on 5432, Mosquitto on 1883
alembic upgrade head

uvicorn app.main:app --reload --port 8000
```

If you don't have Docker, run a local Postgres 16 and Mosquitto (no-auth, port 1883) instead
and point `DATABASE_URL` / `MQTT_BROKER_HOST` at them — nothing in the app is Docker-specific.

## Notable design points

- **Idempotent ingestion**: `SummaryWindow` has a unique constraint on `(node_id, seq)` — MQTT
  QoS 1 can redeliver, so dedup is enforced at the DB layer, not just in application code.
- **Two distinct time windows**: the tracing engine's lookback window (`>= 2 * max(tau_base_s)`,
  derived from topology) is *not* the same thing as storage retention (`RETENTION_HOURS`,
  default 12h, cleaned up by an APScheduler job every 10 minutes). Don't conflate them.
- **Simulation Control Proxy** (`app/api/sim_proxy.py`): a deliberately dumb, isolated router
  that forwards `/simulation/*` calls to `node/`'s orchestrator (`NODE_ORCHESTRATOR_URL`) and
  returns the response verbatim. It exists only so `dashboard/` has a single API base URL to
  call. It touches no database table, no MQTT client, and no tracing code — everything else in
  this service has zero awareness that a simulation exists.
- **CORS defaults to wide open** (`CORS_ALLOW_ORIGINS=*`). Fine for a local hackathon demo; set
  it to your deployed dashboard's actual origin before going further than that.
- **Mosquitto runs with anonymous access enabled** (see `mosquitto.conf`). Same caveat — local
  demo only; a cloud broker should use real credentials (`MQTT_USERNAME`/`MQTT_PASSWORD`) and
  TLS (`MQTT_USE_TLS=true`).

## Tests

```bash
pytest
```

## Deploying (Render + Supabase)

1. **Database**: create a Supabase project. From Project Settings → Database, copy the
   connection string, swap `postgresql://` for `postgresql+asyncpg://`, and append
   `?ssl=require`. Use the **direct connection or Session pooler** — not the Transaction pooler
   (port 6543): asyncpg's server-side prepared statements break under PgBouncer's transaction
   mode.
2. **MQTT broker**: this service and `node/` need one in common. A free
   [HiveMQ Cloud](https://www.hivemq.com/mqtt-cloud-broker/) cluster works well — it gives you a
   host, port 8883, and credentials. Set `MQTT_USE_TLS=true` for any cloud broker.
3. **Render**: New → Web Service, connect this repo (root directory is this repo's root).
   - Build command: `pip install -r requirements.txt`
   - Start command: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Instance type: at least **Starter** — the free tier spins down on idle, which kills the
     persistent MQTT connection and the APScheduler jobs.
   - Environment variables: everything in `.env.example`, with `DATABASE_URL` from step 1,
     `MQTT_*` from step 2, `NODE_ORCHESTRATOR_URL` set to `node/`'s deployed URL (may need to
     deploy `node/` first, or just come back and fill this in after), `PUBLIC_URL` set to this
     service's own Render URL once you have it, and `CORS_ALLOW_ORIGINS` set to your dashboard's
     Vercel/Netlify origin once you have it.
4. Once deployed, hit `GET /health` to confirm it's up, then `GET /nodes` (empty list is
   expected until `node/` registers something).
