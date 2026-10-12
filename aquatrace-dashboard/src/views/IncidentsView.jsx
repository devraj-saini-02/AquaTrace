import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getIncidents } from "../api/client";
import StatusPill from "../components/StatusPill";

const POLL_MS = 5000;

export default function IncidentsView() {
  const [incidents, setIncidents] = useState([]);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    const refresh = () => getIncidents().then(setIncidents).catch((e) => setError(e.message));
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <div className="view-header">
        <h2>Incidents</h2>
        <span className="text-dim">{incidents.length} total</span>
      </div>
      {error && <div className="error-banner">{error}</div>}

      {incidents.length === 0 ? (
        <div className="panel text-dim">No incidents yet. Inject a discharge from Simulation Control to generate one.</div>
      ) : (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th>Contaminant</th>
                <th>Affected nodes</th>
                <th>Top candidate</th>
                <th>Confidence</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((inc) => {
                const top = inc.candidate_causes?.[0];
                return (
                  <tr key={inc.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/incidents/${inc.id}`)}>
                    <td>
                      <Link to={`/incidents/${inc.id}`} onClick={(e) => e.stopPropagation()}>
                        {inc.status}
                      </Link>
                    </td>
                    <td className="mono">{inc.contaminant_id}</td>
                    <td>{inc.affected_node_count}</td>
                    <td>
                      {top ? (
                        <>
                          <span className="mono">{top.node_id}</span> <span className="text-faint">({top.type})</span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{inc.top_confidence != null ? <StatusPill status={inc.top_confidence > 0.7 ? "RED" : inc.top_confidence > 0.3 ? "WARN" : "SAFE"} label={`${Math.round(inc.top_confidence * 100)}%`} /> : "—"}</td>
                    <td className="text-dim" style={{ fontSize: 12 }}>
                      {new Date(inc.updated_at).toLocaleString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
