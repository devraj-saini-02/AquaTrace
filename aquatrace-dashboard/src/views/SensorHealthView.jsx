import { useEffect, useState } from "react";

import { getSensorHealthEvents } from "../api/client";
import StatusPill from "../components/StatusPill";

const POLL_MS = 5000;

function durationLabel(detectedAt, resolvedAt) {
  const end = resolvedAt ? new Date(resolvedAt) : new Date();
  const ms = end - new Date(detectedAt);
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export default function SensorHealthView() {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState("duration");

  useEffect(() => {
    const refresh = () => getSensorHealthEvents().then(setEvents).catch((e) => setError(e.message));
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const sorted = [...events].sort((a, b) => {
    if (sortBy === "duration") {
      const da = (a.resolved_at ? new Date(a.resolved_at) : new Date()) - new Date(a.detected_at);
      const db = (b.resolved_at ? new Date(b.resolved_at) : new Date()) - new Date(b.detected_at);
      return db - da;
    }
    if (sortBy === "open") return (a.resolved_at ? 1 : 0) - (b.resolved_at ? 1 : 0);
    return new Date(b.detected_at) - new Date(a.detected_at);
  });

  const openCount = events.filter((e) => !e.resolved_at).length;

  return (
    <div>
      <div className="view-header">
        <h2>Sensor Health / Replacement Queue</h2>
        <span className="text-dim">{openCount} open · {events.length} total</span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="row" style={{ marginBottom: 12 }}>
        <label className="text-dim" style={{ fontSize: 12 }}>
          Sort by
        </label>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="duration">Duration (longest first)</option>
          <option value="open">Open first</option>
          <option value="recent">Most recently detected</option>
        </select>
      </div>

      {sorted.length === 0 ? (
        <div className="panel text-dim">No sensor health events recorded.</div>
      ) : (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Node</th>
                <th>Contaminant</th>
                <th>State</th>
                <th>Detected</th>
                <th>Resolved</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((ev) => (
                <tr key={ev.id}>
                  <td className="mono">{ev.node_id}</td>
                  <td className="mono">{ev.contaminant_id}</td>
                  <td>
                    <StatusPill status={ev.health_state} />
                  </td>
                  <td className="text-dim" style={{ fontSize: 12 }}>
                    {new Date(ev.detected_at).toLocaleString()}
                  </td>
                  <td className="text-dim" style={{ fontSize: 12 }}>
                    {ev.resolved_at ? new Date(ev.resolved_at).toLocaleString() : <StatusPill status="RED" label="open" />}
                  </td>
                  <td>{durationLabel(ev.detected_at, ev.resolved_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
