const BASE_URL = import.meta.env.VITE_CENTRAL_API_BASE_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ? JSON.stringify(body.detail) : detail;
    } catch {
      // response wasn't JSON; keep statusText
    }
    throw new Error(`${res.status} ${path}: ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---- real data (central-system's own pipeline) ----

export const getNodes = () => request("/nodes");
export const getNode = (nodeId) => request(`/nodes/${encodeURIComponent(nodeId)}`);
export const getEdges = () => request("/edges");
export const getIncidents = () => request("/incidents");
export const getIncident = (id) => request(`/incidents/${id}`);
export const getIncidentTimeseries = (id) => request(`/incidents/${id}/timeseries`);
export const triggerTrace = (contaminantId, window) =>
  request(`/incidents/trace?contaminant_id=${encodeURIComponent(contaminantId)}${window ? `&window=${window}` : ""}`, {
    method: "POST",
  });
export const getModelsForNode = (nodeId) => request(`/models/${encodeURIComponent(nodeId)}`);
export const pushModel = (body) => request("/models/push", { method: "POST", body: JSON.stringify(body) });
export const getSensorHealthEvents = () => request("/sensor-health-events");

// Node registration is keyed by a deterministic node_id (SIM-N001, ...); a fresh simulation run
// whose nodes land at different coordinates than a previous run doesn't overwrite the old row --
// it gets renamed and the stale one lingers forever, polluting the map. Call before simStart().
export const resetTopology = () => request("/admin/reset-topology", { method: "POST" });

// ---- simulation control (§4.6 proxy — still only ever talks to central-system) ----

export const simStart = (body) => request("/simulation/start", { method: "POST", body: JSON.stringify(body) });
export const simStop = () => request("/simulation/stop", { method: "POST" });
export const simInjectEvent = (body) => request("/simulation/inject-event", { method: "POST", body: JSON.stringify(body) });
export const simInjectFault = (body) => request("/simulation/inject-fault", { method: "POST", body: JSON.stringify(body) });
export const simStatus = () => request("/simulation/status");
