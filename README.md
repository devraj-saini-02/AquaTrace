<div align="center">

# 🌊 AquaTrace

https://contamination-dashboard.vercel.app/

### Real-time water contamination detection & source-tracing system

*Find not just **that** a drain network is contaminated — but exactly **which node** it came from.*

[![Node](https://img.shields.io/badge/node-Python%20%2F%20FastAPI-3776AB?logo=python&logoColor=white)](./aquatrace-node)
[![Central System](https://img.shields.io/badge/central--system-FastAPI%20%2F%20Postgres-009688?logo=fastapi&logoColor=white)](./aquatrace-central-system)
[![Dashboard](https://img.shields.io/badge/dashboard-React%20%2F%20Vite-61DAFB?logo=react&logoColor=black)](./aquatrace-dashboard)
[![MQTT](https://img.shields.io/badge/protocol-MQTT-660066?logo=mqtt&logoColor=white)](#-the-wire-protocol)

</div>

---

## 📦 The services

AquaTrace is split into three independently deployable services, each living in its own folder
of this monorepo — 🔗 head into each one for its own setup, deployment, and code-level docs.

| # | Repository | What it is | Stack |
|---|---|---|---|
| 🛰️ | [**aquatrace-node**](./aquatrace-node) | The edge fleet — a simulated network of physical drain-monitoring devices, each running real classification, alerting & OTA logic | Python · FastAPI · scikit-learn · asyncio-mqtt |
| 🧠 | [**aquatrace-central-system**](./aquatrace-central-system) | The brain — MQTT ingestion, Postgres storage, and the mass-balance **tracing engine** that pinpoints contamination sources | Python · FastAPI · SQLAlchemy (async) · Postgres · Alembic |
| 🖥️ | [**aquatrace-dashboard**](./aquatrace-dashboard) | The eyes — a live operator UI: network map, incident/plume playback, sensor health, model registry | React · Vite · react-leaflet · recharts |

None of them share code. `node` and `central-system` only ever agree over the wire (a shared
MQTT protocol spec, implemented independently on each side); `dashboard` only ever talks to
`central-system`'s REST API. Exactly like three real, separately-owned systems would.

---

## 🎯 The problem AquaTrace solves

In a drain / industrial-effluent network, contamination — an illegal chemical discharge, a
sanitation failure, or a sensor fault mimicking one — can enter at *any* node. By the time it's
visible downstream, it's diluted, delayed by travel time, and mixed with everything else in the
flow. A single sensor crossing a threshold tells you **something is wrong upstream** — it
doesn't tell you **where**.

AquaTrace models the drain network as a directed acyclic graph (DAG) of monitoring nodes and
edges (drain reaches), and solves localization with **mass-balance reconciliation**: at every
node, does the contaminant mass actually observed match what its parents' mass — shifted by
travel time and decayed appropriately — would predict? Wherever it doesn't, that's unexplained
mass, and the tracing engine flags it as a contamination-source candidate.

---

## 🏗️ Architecture

```
                              MQTT (register · summary · alert · status · model-update/ack)
   ┌───────────────────────┐ ═══════════════════════════════════════════════════▶ ┌──────────────────────────┐
   │   🛰️  contamination-   │                                                       │  🧠  contamination-      │
   │       node             │ ◀═══════════════════════════════════════════════════ │      central-system      │
   │                         │        OTA model download (plain HTTPS GET)         │                           │
   │  fleet of simulated     │                                                     │  MQTT ingestion → DB     │
   │  edge devices, one      │ ◀───────────────────────────────────────────────────│  → tracing engine → API  │
   │  per drain node         │   simulation-control proxy (demo only, HTTP)        │                           │
   └───────────────────────┘                                                       └────────────┬──────────────┘
                                                                                                    │ REST API
                                                                                                    ▼
                                                                                     ┌──────────────────────────┐
                                                                                     │  🖥️  contamination-      │
                                                                                     │      dashboard           │
                                                                                     │  map · incidents ·       │
                                                                                     │  sensor health · models  │
                                                                                     └──────────────────────────┘
```

**Golden rule of this architecture:** `dashboard` never talks to `node` directly — even the
"press start" simulation controls are forwarded through a narrow, isolated proxy inside
`central-system` (`app/api/sim_proxy.py`), which does nothing but forward the HTTP call
verbatim. It touches no database table, no MQTT client, and no tracing code — the entire real
ingestion/tracing pipeline has **zero awareness** a simulation even exists.

---

## 🛰️ 1. `aquatrace-node` — the edge fleet

Each Virtual Node stands in for one physical device sitting at a point in the drain network.
On every tick, it:

- 📐 **Derives hydraulics** — flow rate (Q), flux, and mass per contaminant, from live
  level/speed via Manning's equation.
- 🧪 **Classifies locally**, two ways at once:
  - **Rule-based** — against registry-defined safe/warn/red thresholds.
  - **ML-based** — a per-contaminant scikit-learn anomaly model, trained offline and shipped
    on-device, hot-swappable via OTA with a shadow-mode evaluation window before a new model is
    trusted.
- 🩺 **Gates on sensor health** — a generic onboard-diagnostic check (range, stuck-value, and
  implausible-jump heuristics) marks a node/contaminant `OK` / `SUSPECT` / `FAILED`; unreliable
  readings are excluded downstream, both locally and by the central tracing engine.
- 🚨 **Alerts intelligently**, not noisily — three trigger types working together:
  - `fast_path` — a single reading extreme enough not to wait for debounce.
  - `debounced` — classic k-of-n debounce (3-of-5) with hysteresis to clear.
  - `cusum` — a CUSUM detector on the residual against a rolling baseline, catching sustained
    loads that never individually cross into WARN/RED.
- 📡 **Publishes** registration, periodic summaries, and alerts over MQTT — exactly as an
  independently-deployed physical device would.

**Contaminants tracked:** pH · temperature · conductivity · TSS · dissolved oxygen · BOD · COD ·
ammoniacal nitrogen · fecal coliform · chromium(VI) · urea — a mix of physical, biological, and
industrial-discharge indicators, each with its own decay behaviour (conservative, reactive,
biologically-reactive, or physically-settling) defined in a shared `registry.json`.

---

## 🧠 2. `aquatrace-central-system` — the control center

The backend of record, and home of the tracing engine. It:

- 📥 **Ingests MQTT idempotently** — QoS-1 can redeliver, so dedup is enforced at the database
  layer (a unique constraint on `node_id + seq`), not just in application code.
- 🗄️ **Persists** readings, topology, and incidents to Postgres via async SQLAlchemy + Alembic
  migrations.
- 🔍 **Runs the tracing engine** on a schedule, walking the node DAG in topological order:

  ```text
  for each node, parents-first:
      M_observed = mass actually measured at this node
      M_expected = Σ over parents of (parent's mass, shifted by travel time τ, decayed by e^(-kτ))
      U          = M_observed − M_expected                     # unexplained mass
      if |U| > 2 × σ_U (propagated uncertainty):
          → flag as a contamination-source candidate
          → classify cause: local injection · dilution loss · pass-through
          → record an Incident with a confidence score
  ```

  Travel time (τ) and decay (k) per edge come from **real hydraulics** — reach geometry is
  first estimated from node geolocation + invert level at registration time, then continuously
  refined from live flow data. Headwater nodes (no parents) fall back to rolling-baseline
  anomaly detection instead, since there's nothing upstream to reconcile against.

- 🌐 **Serves the REST API** the dashboard consumes: nodes, edges, incidents (+ time-series
  playback data for the plume view), sensor health, and the model registry.
- 🔀 **Proxies simulation control** to `node`'s orchestrator through the isolated router
  described above.

---

## 🖥️ 3. `aquatrace-dashboard` — the operator UI

A single-page React/Vite app with five views:

| View | What it shows |
|---|---|
| 🗺️ **Network Map** | Live node/edge health on an OpenStreetMap base layer, polled continuously |
| ⏱️ **Incident List & Detail** | A time-slider **plume playback** — node colour and edge flux animate directly from the tracing engine's own mass/flux numbers, with the top candidate source highlighted throughout |
| 🩺 **Sensor Health / Replacement Queue** | Per-node, per-contaminant reliability table |
| 🔄 **Model Registry** | OTA model versions & shadow-mode status, with a "Push Update" action |
| ▶️ **Simulation Control Panel** | Start/stop the demo network, inject a discharge event or a sensor fault, and watch detection + tracing happen live |

---

## 🔄 End-to-end data flow

1. 📡 A node measures (or, in the demo, simulates) hydraulics and contaminant concentrations,
   and classifies the reading locally.
2. 📤 It publishes a summary — and any alert — over MQTT.
3. 📥 `central-system` ingests and persists it, then periodically runs the tracing engine
   across the current topology.
4. 🚩 Wherever observed mass isn't explained by upstream contributions, an **Incident** is
   recorded with a source candidate and confidence score.
5. 🖥️ `dashboard` queries the REST API and renders the live map, the incident's plume playback,
   and sensor/model health for an operator to act on.

---

## 🔌 The wire protocol

`node` and `central-system` are deliberately **not** sharing a package — both sides implement
the same protocol spec independently (`protocol/schemas.py` / `app/schemas.py`, byte-for-byte
duplicated, not imported), the way two real, separately-owned systems would.

**Topics:**

| Topic | Direction | Purpose |
|---|---|---|
| `nodes/{id}/register` | node → CC | A device announcing itself + its claimed geometry/parents |
| `cc/{id}/register/ack` | CC → node | Assigned parents, baseline model refs, baselining period |
| `nodes/{id}/summary` | node → CC | Periodic hydraulics + per-contaminant readings |
| `nodes/{id}/alert/{contaminant}` | node → CC | A WARN/RED crossing, with its trigger type |
| `nodes/{id}/status` | node → CC | Battery, RSSI, buffer depth |
| `cc/{id}/model/update` | CC → node | Push a new model version for a contaminant |
| `nodes/{id}/model/ack` | node → CC | Confirms load / shadow-mode result |

---

## 🚀 Running the full stack locally

Each service has its own detailed setup guide — here's the short version, in order:

```bash
git clone https://github.com/devraj-saini-02/AquaTrace && cd AquaTrace
# Each service runs in its own terminal, starting from the repo root.

# 1️⃣  central-system — Postgres + Mosquitto, migrations, then the API
cd aquatrace-central-system
docker-compose up -d && alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 2️⃣  node — the simulated device fleet + its orchestrator
cd aquatrace-node
uvicorn orchestrator:app --reload --port 8100

# 3️⃣  dashboard — the operator UI
cd aquatrace-dashboard
npm install && npm run dev   # → http://localhost:5173
```

For cloud deployment (Render + Supabase for `central-system`, Render for `node`,
Vercel/Netlify for `dashboard`, HiveMQ Cloud as the shared MQTT broker), see the **Deploying**
section in each service's own README. ☁️

---

## 🎛️ About the simulation

There's no real sensor hardware deployed for this build, so `aquatrace-node` stands in a
physics-based **World Simulator** — hydraulics, advection-dispersion-reaction transport, diurnal
contamination baselines, and injectable discharge/fault events — for what an actual field device
would measure. Everything *downstream* of that fake input is real, production-shaped code:
classification, alerting, health gating, the MQTT protocol, OTA updates, and the entire tracing
engine in `central-system` would run unchanged against genuine sensors. The dashboard's
Simulation Control Panel just lets an operator start the fleet and inject events for demo
purposes — it has no bearing on how detection or tracing actually works. 🧪
