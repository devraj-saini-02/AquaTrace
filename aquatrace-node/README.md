# node

Simulates a fleet of physical drain-monitoring nodes for the hackathon demo. Nothing here is a
real sensor: `simulator/world.py` is a physics model of the drain network (hydraulics,
advection-dispersion-reaction transport, diurnal contamination baselines) standing in for what a
deployed sensor would have measured. Everything downstream of that fake input — node
classification logic, MQTT protocol handling, alerting, sensor-health gating, OTA model
updates — is real, production-shaped code, identical in spirit to what would run on an actual
device. This is a fully self-contained repo/service: it does not import anything from
`central-system/`; the only coupling is over MQTT (the wire protocol) plus, for OTA model
downloads, a plain HTTP GET against `central-system/`'s API.

## Stack

FastAPI (control-plane endpoints only — this is not a data API), Pydantic v2, `asyncio-mqtt`,
scikit-learn (per-contaminant anomaly models), numpy/pandas (the physics simulator).

## Layout

```
simulator/
  clock.py            SimClock — the one shared simulated-time source, see README §Simulated time
  world.py              DAG builder, diurnal baselines, Gaussian-slug propagation kernel,
                          inject_event, inject_sensor_fault
core/
  virtual_node.py       one instance per simulated physical node: Q/flux/mass, classification,
                          sensor-health gating, alerting (debounce/hysteresis/fast-path/CUSUM),
                          summary buffering
protocol/
  schemas.py             wire Pydantic models — duplicated verbatim from central-system's
                          schemas.py, not imported. This is intentional: the two services only
                          agree on shape because both sides implement the same spec, exactly as
                          two independently-deployed real systems would.
  mqtt_client.py           publish/subscribe wrapper, OTA download/verify/shadow/hot-swap
ml/
  inference.py              loads a model per contaminant, predict(), supports hot-swap
  artifacts/                 trained model pickles + checksums (gitignored; run training/ to produce)
training/
  generate_dataset.py         offline: runs the simulator over a long synthetic period, dumps
                                 labeled data (see README §Model training for what "labeled" means here)
  train.py                      trains one small model per contaminant
config/registry.json           duplicated verbatim from central-system/config/registry.json
orchestrator.py                  FastAPI app — the ONLY place that knows a simulation exists
```

## Local setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

uvicorn orchestrator:app --reload --port 8100
```

The orchestrator does not auto-start a simulation. Call `POST /simulation/start` (directly, or
via `dashboard/`'s Simulation Control Panel through `central-system/`'s proxy) once
`central-system/` is up and its Mosquitto broker is reachable.

## Model training (offline, disclosed simplification)

`training/generate_dataset.py` runs the World Simulator over a long synthetic period, sweeping
baselines plus many injected events/faults, and labels each row two ways: `rule_label` straight
from the registry thresholds, and `anomaly_zscore_label` — a z-score against that node's own
rolling diurnal baseline. **This z-score is the honest stand-in for a human labeler** — there is
no real labeling step in this hackathon build, and this file says so again at the top of the
script itself.

`training/train.py` trains one lightweight `MLPClassifier`/gradient-boosting model per
contaminant on that dataset and writes `ml/artifacts/{contaminant_id}_{version}.pkl` plus a
`.sha256` checksum file. The intended production design is a single shared-trunk multi-head
model; training N small independent models here is a disclosed hackathon simplification, not an
architectural recommendation.

Run both from `node/` (they need `node/` on `sys.path`, which module-mode invocation gives you
for free):

```bash
python3 -m training.generate_dataset --node-count 10 --sim-days 2 --events 150 --faults 60
python3 -m training.train --version v1
```

Generation runs synchronously with no real-time sleeping (ticks are constructed directly, not
paced against `SIM_TIME_SCALE`), so a multi-day synthetic period takes seconds. `ModelStore`
(`ml/inference.py`) bootstraps whatever's in `ml/artifacts/` at process start, before any MQTT
traffic — a node ships with a pretrained model already on board, exactly like a real device.
Because of that, `ContaminantReading.model_ver` on the wire always reflects whichever model is
*actually loaded and producing predictions* (`ModelStore.active[...].version`), not whatever
`central-system/` last acknowledged at registration or OTA — those can be stale the moment a
local model loads independently, before the node has heard from CC at all.

## Simulated time

Everything timestamped on the wire (`t_start`, `t_end`, `t_max`, alert `timestamp`) comes from
`simulator/clock.py`'s `SimClock.now()` — never wall-clock time. `SimClock` advances at
`SIM_TIME_SCALE` simulated-seconds per real second (`.env`), ticked by an asyncio loop. This is
the only clock in the system; `central-system/` does not need an equivalent — it just stamps its
own `received_at` bookkeeping column with real wall-clock time and otherwise trusts the
timestamps it receives.

## Tests

```bash
pytest
```

## Deploying (Render)

Needs the same MQTT broker `central-system/` is using, and `central-system/` deployed first
(or deploy both, then circle back to fill in each other's URLs).

1. Render: New → Web Service, connect this repo (root directory is this repo's root).
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn orchestrator:app --host 0.0.0.0 --port $PORT`
   - Instance type: at least **Starter** — this process holds a live asyncio task per
     simulated node plus persistent MQTT connections; the free tier's idle spin-down kills
     a running simulation.
   - Environment variables: everything in `.env.example`, with `MQTT_*` matching
     `central-system/`'s broker exactly (same host/port/credentials/`MQTT_USE_TLS`), and
     `CC_API_BASE_URL` set to `central-system/`'s deployed URL.
2. The `ml/artifacts/*_v1.{pkl,sha256}` baseline models are committed to this repo, so a fresh
   deploy has real ML classification from the start — no training step required before demoing.
3. `GET /simulation/status` confirms it's up; `POST /simulation/start` from `dashboard/`'s
   Simulation Control Panel (via `central-system/`'s proxy) is the normal way to kick things off.
