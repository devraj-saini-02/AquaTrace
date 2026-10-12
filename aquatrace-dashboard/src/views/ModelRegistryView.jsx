import { useEffect, useState } from "react";

import { getModelsForNode, getNodes, pushModel } from "../api/client";
import StatusPill from "../components/StatusPill";

const POLL_MS = 6000;

export default function ModelRegistryView() {
  const [rows, setRows] = useState([]);
  const [nodeIds, setNodeIds] = useState([]);
  const [error, setError] = useState(null);

  const refresh = async () => {
    try {
      const nodes = await getNodes();
      setNodeIds(nodes.map((n) => n.node_id));
      const perNode = await Promise.all(
        nodes.map((n) =>
          getModelsForNode(n.node_id)
            .then((models) => models)
            .catch(() => [])
        )
      );
      setRows(perNode.flat());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const sorted = [...rows].sort((a, b) => new Date(b.pushed_at) - new Date(a.pushed_at));

  return (
    <div>
      <div className="view-header">
        <h2>Model Registry</h2>
        <span className="text-dim">{rows.length} versions across {nodeIds.length} nodes</span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="grid-2">
        <div className="panel" style={{ padding: 0, gridColumn: "1 / -1" }}>
          {sorted.length === 0 ? (
            <div className="text-dim" style={{ padding: 16 }}>
              No model versions pushed yet.
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Node</th>
                  <th>Contaminant</th>
                  <th>Version</th>
                  <th>Running</th>
                  <th>Shadow disagreement</th>
                  <th>Pushed</th>
                  <th>Acked</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((m) => (
                  <tr key={m.id}>
                    <td className="mono">{m.node_id}</td>
                    <td className="mono">{m.contaminant_id}</td>
                    <td className="mono">{m.version}</td>
                    <td>{m.running ? <StatusPill status="SAFE" label="running" /> : <StatusPill status="OFFLINE" label="not running" />}</td>
                    <td>{m.shadow_disagreement_rate != null ? `${(m.shadow_disagreement_rate * 100).toFixed(1)}%` : "—"}</td>
                    <td className="text-dim" style={{ fontSize: 12 }}>
                      {new Date(m.pushed_at).toLocaleString()}
                    </td>
                    <td className="text-dim" style={{ fontSize: 12 }}>
                      {m.acked_at ? new Date(m.acked_at).toLocaleString() : <StatusPill status="WARN" label="pending" />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <PushUpdateForm nodeIds={nodeIds} onPushed={refresh} />
      </div>
    </div>
  );
}

const CONTAMINANT_IDS = ["ph", "temperature", "conductivity", "tss", "dissolved_oxygen", "bod", "cod", "ammoniacal_n", "chromium_vi", "urea"];

function PushUpdateForm({ nodeIds, onPushed }) {
  const [nodeId, setNodeId] = useState("");
  const [contaminantId, setContaminantId] = useState("tss");
  const [modelPath, setModelPath] = useState("");
  const [modelVersion, setModelVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);

  const effectiveNodeId = nodeId || nodeIds[0] || "";

  async function submit() {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await pushModel({ node_id: effectiveNodeId, contaminant_id: contaminantId, model_path: modelPath, model_version: modelVersion });
      setSuccess(`Pushed ${contaminantId} v${modelVersion} to ${effectiveNodeId} — watch for the ack (shadow mode runs for a few ticks).`);
      onPushed();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="text-dim" style={{ fontSize: 12, marginBottom: 8 }}>
        Push Update
      </div>
      {error && <div className="error-banner">{error}</div>}
      {success && <div className="text-dim" style={{ fontSize: 12, marginBottom: 12 }}>{success}</div>}
      <div className="form-row">
        <label>Node</label>
        <select value={effectiveNodeId} onChange={(e) => setNodeId(e.target.value)}>
          {nodeIds.length === 0 && <option value="">(no nodes registered)</option>}
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
        <label>Model file path (on central-system's machine)</label>
        <input placeholder="/path/to/node/ml/artifacts/tss_v2.pkl" value={modelPath} onChange={(e) => setModelPath(e.target.value)} />
      </div>
      <div className="form-row">
        <label>Version label</label>
        <input placeholder="v2" value={modelVersion} onChange={(e) => setModelVersion(e.target.value)} />
      </div>
      <button className="primary" disabled={busy || !effectiveNodeId || !modelPath || !modelVersion} onClick={submit}>
        Push
      </button>
    </div>
  );
}
