import { useCallback, useEffect, useState } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from "react-leaflet";

import { getEdges, getModelsForNode, getNode, getNodes } from "../api/client";
import FitBounds from "../components/FitBounds";
import StatusPill from "../components/StatusPill";
import { colorForStatus } from "../components/status";

const POLL_MS = 4000;
const DELHI_CENTER = [28.6, 77.03]; // roughly the Najafgarh drain corridor -- see world.py
const STATE_RANK = { SAFE: 0, WARN: 1, RED: 2 };

function worstContaminantState(detail) {
  const contaminants = detail?.latest_summary?.contaminants || [];
  let worst = "SAFE";
  for (const c of contaminants) {
    if (STATE_RANK[c.final_state] > STATE_RANK[worst]) worst = c.final_state;
  }
  return worst;
}

function nodeColor(node, detail) {
  if (node.status !== "active") return colorForStatus(node.status); // offline/baselining -> grey
  return colorForStatus(detail ? worstContaminantState(detail) : "SAFE");
}

export default function NetworkMapView() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [details, setDetails] = useState({});
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [nodeList, edgeList] = await Promise.all([getNodes(), getEdges()]);
      setNodes(nodeList);
      setEdges(edgeList);
      setError(null);
      const detailPairs = await Promise.all(
        nodeList.map((n) =>
          getNode(n.node_id)
            .then((d) => [n.node_id, d])
            .catch(() => [n.node_id, null])
        )
      );
      setDetails(Object.fromEntries(detailPairs));
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const nodeById = Object.fromEntries(nodes.map((n) => [n.node_id, n]));
  const center = nodes.length
    ? [nodes.reduce((s, n) => s + n.latitude, 0) / nodes.length, nodes.reduce((s, n) => s + n.longitude, 0) / nodes.length]
    : DELHI_CENTER;

  return (
    <div>
      <div className="view-header">
        <h2>Network Map</h2>
        <span className="text-dim">{nodes.length} nodes</span>
      </div>
      {error && <div className="error-banner">Couldn't reach central-system: {error}</div>}
      <div className="map-layout" style={{ display: "flex", gap: 16 }}>
        <div
          className="map-container-wrap"
          style={{ flex: 1, height: "calc(100vh - 110px)", borderRadius: 6, overflow: "hidden", border: "1px solid var(--border)" }}
        >
          <MapContainer center={center} zoom={13} style={{ height: "100%", width: "100%", background: "var(--bg)" }}>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <FitBounds points={nodes.map((n) => [n.latitude, n.longitude])} />
            {edges.map((e) => {
              const p = nodeById[e.parent_node_id];
              const c = nodeById[e.child_node_id];
              if (!p || !c) return null;
              return (
                <Polyline
                  key={e.id}
                  positions={[
                    [p.latitude, p.longitude],
                    [c.latitude, c.longitude],
                  ]}
                  pathOptions={{
                    color: e.validated ? "var(--accent)" : "var(--text-faint)",
                    weight: e.validated ? 3 : 2,
                    dashArray: e.validated ? "10 8" : "4 5",
                    className: e.validated ? "flow-edge" : "flow-edge-slow",
                  }}
                >
                  <Tooltip sticky>
                    {e.parent_node_id} → {e.child_node_id} ({Math.round(e.length_m)}m, τ≈{Math.round(e.tau_base_s)}s
                    {e.validated ? ", validated" : ""})
                  </Tooltip>
                </Polyline>
              );
            })}
            {nodes.map((n) => (
              <CircleMarker
                key={n.node_id}
                center={[n.latitude, n.longitude]}
                radius={selected === n.node_id ? 10 : 7}
                pathOptions={{
                  color: nodeColor(n, details[n.node_id]),
                  fillColor: nodeColor(n, details[n.node_id]),
                  fillOpacity: 0.85,
                  weight: 2,
                }}
                eventHandlers={{ click: () => setSelected(n.node_id) }}
              >
                <Tooltip>{n.node_id}</Tooltip>
              </CircleMarker>
            ))}
          </MapContainer>
        </div>
        <div className="side-panel" style={{ width: 320, flexShrink: 0 }}>
          <NodeDetailPanel nodeId={selected} detail={selected ? details[selected] : null} onClose={() => setSelected(null)} />
        </div>
      </div>
    </div>
  );
}

function NodeDetailPanel({ nodeId, detail, onClose }) {
  const [models, setModels] = useState([]);

  useEffect(() => {
    if (!nodeId) return;
    getModelsForNode(nodeId)
      .then(setModels)
      .catch(() => setModels([]));
  }, [nodeId]);

  if (!nodeId) {
    return <div className="panel text-dim">Click a node to see details.</div>;
  }
  if (!detail) {
    return <div className="panel text-dim">Loading {nodeId}…</div>;
  }

  return (
    <div className="panel stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{detail.node_id}</strong>
        <button onClick={onClose}>×</button>
      </div>
      <StatusPill status={detail.status} />
      <div className="text-dim mono" style={{ fontSize: 12 }}>
        {detail.latitude.toFixed(4)}, {detail.longitude.toFixed(4)}
      </div>
      <div className="text-dim" style={{ fontSize: 12 }}>
        parents: {detail.parent_ids.join(", ") || "none (headwater)"}
        <br />
        children: {detail.child_ids.join(", ") || "none (outlet)"}
      </div>

      {detail.latest_summary ? (
        <div>
          <div className="text-dim" style={{ fontSize: 12, marginBottom: 6 }}>
            level {detail.latest_summary.level_m.toFixed(2)}m · speed {detail.latest_summary.speed_mps.toFixed(2)}m/s · Q{" "}
            {detail.latest_summary.q_m3s.toFixed(2)}m³/s
          </div>
          <table>
            <thead>
              <tr>
                <th>Contaminant</th>
                <th>Mean</th>
                <th>State</th>
                <th>Health</th>
              </tr>
            </thead>
            <tbody>
              {detail.latest_summary.contaminants.map((c) => (
                <tr key={c.contaminant_id}>
                  <td>{c.contaminant_id}</td>
                  <td>{c.mean.toFixed(2)}</td>
                  <td>
                    <StatusPill status={c.final_state} />
                  </td>
                  <td>
                    <StatusPill status={c.sensor_health} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-faint">no summary received yet</div>
      )}

      {models.length > 0 && (
        <div>
          <div className="text-dim" style={{ fontSize: 12, marginBottom: 6 }}>
            Model versions
          </div>
          <table>
            <thead>
              <tr>
                <th>Contaminant</th>
                <th>Version</th>
                <th>Running</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.id}>
                  <td>{m.contaminant_id}</td>
                  <td>{m.version}</td>
                  <td>{m.running ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
