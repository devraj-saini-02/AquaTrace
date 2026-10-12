import { useCallback, useEffect, useState } from "react";

import { resetTopology, simInjectEvent, simInjectFault, simStart, simStatus, simStop } from "../api/client";

const CONTAMINANT_IDS = ["ph", "temperature", "conductivity", "tss", "dissolved_oxygen", "bod", "cod", "ammoniacal_n", "chromium_vi", "urea"];
const FAULT_TYPES = ["stuck", "dropout", "spike"];
const POLL_MS = 3000;

export default function SimulationControlView() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    simStatus()
      .then(setStatus)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  async function withBusy(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="view-header">
        <h2>Simulation Control</h2>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="grid-2">
        <div className="panel stack">
          <StatusReadout status={status} />
          <StartStopForm
            busy={busy}
            running={status?.running}
            onStart={(body) =>
              withBusy(async () => {
                await resetTopology();
                await simStart(body);
              })
            }
            onStop={() => withBusy(() => simStop())}
          />
        </div>

        <div className="stack">
          <InjectEventForm busy={busy || !status?.running} nodeIds={status?.node_ids || []} onSubmit={(body) => withBusy(() => simInjectEvent(body))} />
          <InjectFaultForm busy={busy || !status?.running} nodeIds={status?.node_ids || []} onSubmit={(body) => withBusy(() => simInjectFault(body))} />
        </div>
      </div>
    </div>
  );
}

function StatusReadout({ status }) {
  if (!status) return <div className="text-dim">loading status…</div>;
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{status.running ? "Running" : "Stopped"}</strong>
        <span className="status-dot" style={{ background: status.running ? "var(--safe)" : "var(--offline)", width: 10, height: 10 }} />
      </div>
      <div className="text-dim mono" style={{ fontSize: 12, marginTop: 8 }}>
        sim time: {status.sim_time || "—"}
        <br />
        nodes: {status.node_count}
        <br />
        seed: {status.seed ?? "—"} · scale: {status.sim_time_scale ?? "—"}x
      </div>
    </div>
  );
}

function StartStopForm({ busy, running, onStart, onStop }) {
  const [nodeCount, setNodeCount] = useState(15);
  const [simTimeScale, setSimTimeScale] = useState(120);
  const [seed, setSeed] = useState(42);
  const [autoInjectDemoEvents, setAutoInjectDemoEvents] = useState(true);

  return (
    <div>
      <div className="form-row">
        <label>Node count</label>
        <input type="number" min="2" max="25" value={nodeCount} onChange={(e) => setNodeCount(+e.target.value)} disabled={running} />
      </div>
      <div className="form-row">
        <label>Sim time scale (sim-seconds per real second)</label>
        <input type="number" min="1" value={simTimeScale} onChange={(e) => setSimTimeScale(+e.target.value)} disabled={running} />
      </div>
      <div className="form-row">
        <label>Seed</label>
        <input type="number" value={seed} onChange={(e) => setSeed(+e.target.value)} disabled={running} />
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <input
          type="checkbox"
          id="auto-inject"
          checked={autoInjectDemoEvents}
          onChange={(e) => setAutoInjectDemoEvents(e.target.checked)}
          disabled={running}
          style={{ width: "auto" }}
        />
        <label htmlFor="auto-inject" className="text-dim" style={{ fontSize: 12 }}>
          Auto-inject demo contamination events (guarantees WARN/RED nodes + alerts shortly after start)
        </label>
      </div>
      <div className="row">
        {!running ? (
          <button
            className="success"
            disabled={busy}
            onClick={() => onStart({ node_count: nodeCount, sim_time_scale: simTimeScale, seed, auto_inject_demo_events: autoInjectDemoEvents })}
          >
            Start
          </button>
        ) : (
          <button disabled={busy} onClick={onStop}>
            Stop
          </button>
        )}
      </div>
    </div>
  );
}

function InjectEventForm({ busy, nodeIds, onSubmit }) {
  const [nodeId, setNodeId] = useState("");
  const [contaminantId, setContaminantId] = useState("tss");
  const [magnitude, setMagnitude] = useState(300);
  const [startOffset, setStartOffset] = useState(30);
  const [duration, setDuration] = useState(900);
  const [sharpness, setSharpness] = useState(1.5);

  const effectiveNodeId = nodeId || nodeIds[0] || "";

  return (
    <div className="panel">
      <div className="text-dim" style={{ fontSize: 12, marginBottom: 8 }}>
        Inject Discharge
      </div>
      <div className="form-row">
        <label>Node</label>
        <select value={effectiveNodeId} onChange={(e) => setNodeId(e.target.value)}>
          {nodeIds.length === 0 && <option value="">(start simulation first)</option>}
          {nodeIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>Contaminant</label>
        <select value={contaminantId} onChange={(e) => setContaminantId(e.target.value)}>
          {CONTAMINANT_IDS.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>Magnitude (added on top of baseline)</label>
        <input type="number" value={magnitude} onChange={(e) => setMagnitude(+e.target.value)} />
      </div>
      <div className="form-row">
        <label>Start offset (sim-seconds from now)</label>
        <input type="number" value={startOffset} onChange={(e) => setStartOffset(+e.target.value)} />
      </div>
      <div className="form-row">
        <label>Duration (sim-seconds)</label>
        <input type="number" value={duration} onChange={(e) => setDuration(+e.target.value)} />
      </div>
      <div className="form-row">
        <label>Onset sharpness</label>
        <input type="number" step="0.1" value={sharpness} onChange={(e) => setSharpness(+e.target.value)} />
      </div>
      <button
        className="primary"
        disabled={busy || !effectiveNodeId}
        onClick={() =>
          onSubmit({
            node_id: effectiveNodeId,
            contaminant_id: contaminantId,
            magnitude,
            start_offset_s: startOffset,
            duration_s: duration,
            onset_sharpness: sharpness,
          })
        }
      >
        Inject Discharge
      </button>
    </div>
  );
}

function InjectFaultForm({ busy, nodeIds, onSubmit }) {
  const [nodeId, setNodeId] = useState("");
  const [contaminantId, setContaminantId] = useState("tss");
  const [faultType, setFaultType] = useState("spike");
  const [startOffset, setStartOffset] = useState(10);
  const [duration, setDuration] = useState(600);

  const effectiveNodeId = nodeId || nodeIds[0] || "";

  return (
    <div className="panel">
      <div className="text-dim" style={{ fontSize: 12, marginBottom: 8 }}>
        Inject Sensor Fault
      </div>
      <div className="form-row">
        <label>Node</label>
        <select value={effectiveNodeId} onChange={(e) => setNodeId(e.target.value)}>
          {nodeIds.length === 0 && <option value="">(start simulation first)</option>}
          {nodeIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>Contaminant</label>
        <select value={contaminantId} onChange={(e) => setContaminantId(e.target.value)}>
          {CONTAMINANT_IDS.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>Fault type</label>
        <select value={faultType} onChange={(e) => setFaultType(e.target.value)}>
          {FAULT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <label>Start offset (sim-seconds from now)</label>
        <input type="number" value={startOffset} onChange={(e) => setStartOffset(+e.target.value)} />
      </div>
      <div className="form-row">
        <label>Duration (sim-seconds)</label>
        <input type="number" value={duration} onChange={(e) => setDuration(+e.target.value)} />
      </div>
      <button
        disabled={busy || !effectiveNodeId}
        onClick={() =>
          onSubmit({ node_id: effectiveNodeId, contaminant_id: contaminantId, fault_type: faultType, start_offset_s: startOffset, duration_s: duration })
        }
      >
        Inject Fault
      </button>
    </div>
  );
}
