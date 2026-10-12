import { useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from "react-leaflet";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from "recharts";
import { Link, useParams } from "react-router-dom";

import { getIncident, getIncidentTimeseries, getNodes } from "../api/client";
import FitBounds from "../components/FitBounds";
import StatusPill from "../components/StatusPill";
import { colorForStatus } from "../components/status";

const PLAY_TICK_MS = 200;
const PLAYBACK_STEP_MS = 30000; // one summary cycle's worth of sim-time per tick, before `speed`

function groupByNode(points) {
  const map = {};
  for (const p of points) (map[p.node_id] ??= []).push(p);
  for (const k in map) map[k].sort((a, b) => new Date(a.t) - new Date(b.t));
  return map;
}

function groupByEdge(points) {
  const map = {};
  for (const p of points) {
    const key = `${p.parent_id}->${p.child_id}`;
    (map[key] ??= []).push(p);
  }
  for (const k in map) map[k].sort((a, b) => new Date(a.t) - new Date(b.t));
  return map;
}

function nearest(sorted, targetMs) {
  if (!sorted || sorted.length === 0) return null;
  let best = sorted[0];
  let bestDiff = Math.abs(new Date(best.t).getTime() - targetMs);
  for (const p of sorted) {
    const diff = Math.abs(new Date(p.t).getTime() - targetMs);
    if (diff < bestDiff) {
      best = p;
      bestDiff = diff;
    }
  }
  return best;
}

export default function IncidentDetailView() {
  const { id } = useParams();
  const [incident, setIncident] = useState(null);
  const [timeseries, setTimeseries] = useState(null);
  const [positions, setPositions] = useState({});
  const [error, setError] = useState(null);

  const [sliderMs, setSliderMs] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    setIncident(null);
    setTimeseries(null);
    setSliderMs(null);
    setError(null);
    Promise.all([getIncident(id), getIncidentTimeseries(id), getNodes()])
      .then(([inc, ts, nodes]) => {
        setIncident(inc);
        setTimeseries(ts);
        setPositions(Object.fromEntries(nodes.map((n) => [n.node_id, { lat: n.latitude, lon: n.longitude }])));
        setSliderMs(new Date(ts.t_start).getTime());
      })
      .catch((e) => setError(e.message));
  }, [id]);

  const tStartMs = timeseries ? new Date(timeseries.t_start).getTime() : 0;
  const tEndMs = timeseries ? new Date(timeseries.t_end).getTime() : 0;

  const nodeSeries = useMemo(() => (timeseries ? groupByNode(timeseries.nodes) : {}), [timeseries]);
  const edgeSeries = useMemo(() => (timeseries ? groupByEdge(timeseries.edges) : {}), [timeseries]);
  const maxFlux = useMemo(() => {
    if (!timeseries || timeseries.edges.length === 0) return 1;
    return Math.max(1e-9, ...timeseries.edges.map((e) => e.flux_g_s));
  }, [timeseries]);

  useEffect(() => {
    if (!playing || sliderMs == null) return;
    const timer = setInterval(() => {
      setSliderMs((prev) => {
        const next = prev + PLAYBACK_STEP_MS * speed;
        if (next >= tEndMs) {
          setPlaying(false);
          return tEndMs;
        }
        return next;
      });
    }, PLAY_TICK_MS);
    return () => clearInterval(timer);
  }, [playing, speed, tEndMs, sliderMs]);

  if (error) {
    return (
      <div>
        <BackLink />
        <div className="error-banner">{error}</div>
      </div>
    );
  }
  if (!incident || !timeseries || sliderMs == null) {
    return (
      <div>
        <BackLink />
        <div className="text-dim">loading…</div>
      </div>
    );
  }

  const topCandidate = incident.candidate_causes?.[0];
  const sourceNodeId = topCandidate?.node_id;
  const nodeIds = Object.keys(nodeSeries);
  const centerPts = nodeIds.map((nid) => positions[nid]).filter(Boolean);
  const center = centerPts.length
    ? [centerPts.reduce((s, p) => s + p.lat, 0) / centerPts.length, centerPts.reduce((s, p) => s + p.lon, 0) / centerPts.length]
    : [28.6, 77.03]; // roughly the Najafgarh drain corridor -- see node/simulator/world.py

  return (
    <div>
      <BackLink />
      <div className="view-header">
        <h2>
          Incident — <span className="mono">{incident.contaminant_id}</span>
        </h2>
        <span className="text-dim">
          {incident.status} · {incident.affected_node_count} nodes
        </span>
      </div>

      <div className="map-layout" style={{ display: "flex", gap: 16 }}>
        <div style={{ flex: 1 }}>
          <div className="map-container-wrap" style={{ height: "56vh", borderRadius: 6, overflow: "hidden", border: "1px solid var(--border)" }}>
            <MapContainer center={center} zoom={13} style={{ height: "100%", width: "100%", background: "var(--bg)" }}>
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <FitBounds points={centerPts.map((p) => [p.lat, p.lon])} />
              {Object.entries(edgeSeries).map(([key, series]) => {
                const [parentId, childId] = key.split("->");
                const p = positions[parentId];
                const c = positions[childId];
                if (!p || !c) return null;
                const point = nearest(series, sliderMs);
                const flux = point?.flux_g_s || 0;
                const frac = Math.min(flux / maxFlux, 1);
                const flowClass = frac > 0.5 ? "flow-edge-fast" : frac > 0.08 ? "flow-edge" : "flow-edge-slow";
                return (
                  <Polyline
                    key={key}
                    positions={[
                      [p.lat, p.lon],
                      [c.lat, c.lon],
                    ]}
                    pathOptions={{
                      color: "var(--accent)",
                      opacity: 0.12 + 0.75 * frac,
                      weight: 2 + 8 * frac,
                      dashArray: "10 8",
                      className: flowClass,
                    }}
                  >
                    <Tooltip sticky>
                      {parentId} → {childId}: {flux.toFixed(2)} g/s
                    </Tooltip>
                  </Polyline>
                );
              })}
              {nodeIds.map((nid) => {
                const pos = positions[nid];
                if (!pos) return null;
                const point = nearest(nodeSeries[nid], sliderMs);
                const color = colorForStatus(point?.final_state || "SAFE");
                return (
                  <CircleMarker
                    key={nid}
                    center={[pos.lat, pos.lon]}
                    radius={7}
                    pathOptions={{ color, fillColor: color, fillOpacity: 0.85, weight: 2 }}
                  >
                    <Tooltip>
                      {nid}
                      {point ? `: ${point.concentration.toFixed(1)} (${point.final_state}), flux ${point.flux_g_s.toFixed(2)} g/s` : ": no data at this time"}
                    </Tooltip>
                  </CircleMarker>
                );
              })}
              {sourceNodeId && positions[sourceNodeId] && <PulsingMarker position={positions[sourceNodeId]} />}
            </MapContainer>
          </div>

          <div className="panel" style={{ marginTop: 16 }}>
            <PlaybackControls
              tStartMs={tStartMs}
              tEndMs={tEndMs}
              sliderMs={sliderMs}
              onChange={(ms) => {
                setPlaying(false);
                setSliderMs(ms);
              }}
              playing={playing}
              onTogglePlay={() => setPlaying((p) => !p)}
              speed={speed}
              onSpeedChange={setSpeed}
            />
          </div>

          <ConcentrationChart nodeSeries={nodeSeries} sourceNodeId={sourceNodeId} sliderMs={sliderMs} />
        </div>

        <div className="side-panel" style={{ width: 340, flexShrink: 0 }}>
          <CandidateCauseList causes={incident.candidate_causes} />
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <div style={{ marginBottom: 12 }}>
      <Link to="/incidents">&larr; all incidents</Link>
    </div>
  );
}

function PulsingMarker({ position }) {
  const reducedMotion = useMemo(() => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches, []);
  const [phase, setPhase] = useState(0);
  useEffect(() => {
    if (reducedMotion) return; // still-visible, still-persistent highlight -- just not animated
    const id = setInterval(() => setPhase((p) => (p + 0.15) % (Math.PI * 2)), 60);
    return () => clearInterval(id);
  }, [reducedMotion]);
  const radius = reducedMotion ? 15 : 14 + 5 * Math.sin(phase);
  const opacity = reducedMotion ? 0.4 : 0.3 + 0.25 * Math.sin(phase);
  return (
    <CircleMarker
      center={[position.lat, position.lon]}
      radius={radius}
      pathOptions={{ color: "var(--accent)", weight: 2, fillOpacity: opacity, fillColor: "var(--accent)" }}
      interactive={false}
    />
  );
}

function ConcentrationChart({ nodeSeries, sourceNodeId, sliderMs }) {
  const series = nodeSeries[sourceNodeId];
  if (!series || series.length === 0) return null;
  const data = series.map((p) => ({ t: new Date(p.t).getTime(), concentration: p.concentration }));

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="text-dim" style={{ fontSize: 12, marginBottom: 8 }}>
        Concentration over time — <span className="mono">{sourceNodeId}</span> (top candidate)
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ left: -10, right: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => new Date(v).toLocaleTimeString()}
            stroke="var(--text-faint)"
            fontSize={11}
          />
          <YAxis stroke="var(--text-faint)" fontSize={11} width={50} />
          <ChartTooltip
            contentStyle={{ background: "var(--bg-panel)", border: "1px solid var(--border)", fontSize: 12 }}
            labelFormatter={(v) => new Date(v).toLocaleString()}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="concentration" stroke="var(--accent)" dot={false} strokeWidth={2} name="concentration" />
          <ReferenceLine x={sliderMs} stroke="var(--text)" strokeDasharray="4 4" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function PlaybackControls({ tStartMs, tEndMs, sliderMs, onChange, playing, onTogglePlay, speed, onSpeedChange }) {
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <div className="row">
          <button className="primary" onClick={onTogglePlay} disabled={sliderMs >= tEndMs && !playing}>
            {playing ? "Pause" : "Play"}
          </button>
          <select value={speed} onChange={(e) => onSpeedChange(+e.target.value)}>
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
            <option value={4}>4x</option>
          </select>
        </div>
        <span className="mono text-dim">{new Date(sliderMs).toLocaleString()}</span>
      </div>
      <input type="range" min={tStartMs} max={tEndMs} value={sliderMs} onChange={(e) => onChange(+e.target.value)} style={{ width: "100%" }} />
      <div className="row text-faint" style={{ justifyContent: "space-between", fontSize: 11 }}>
        <span>{new Date(tStartMs).toLocaleString()}</span>
        <span>{new Date(tEndMs).toLocaleString()}</span>
      </div>
    </div>
  );
}

function CandidateCauseList({ causes }) {
  return (
    <div className="panel stack">
      <div className="text-dim" style={{ fontSize: 12 }}>
        Candidate causes (ranked by confidence)
      </div>
      {(!causes || causes.length === 0) && <div className="text-faint">none</div>}
      {(causes || []).map((c, i) => (
        <div key={c.node_id} className="panel" style={{ background: "var(--bg-panel-raised)", padding: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong className="mono">
              {i === 0 ? "★ " : ""}
              {c.node_id}
            </strong>
            <StatusPill
              status={c.confidence > 0.7 ? "RED" : c.confidence > 0.3 ? "WARN" : "SAFE"}
              label={`${Math.round(c.confidence * 100)}%`}
            />
          </div>
          <div className="text-faint" style={{ fontSize: 11, marginTop: 4, textTransform: "uppercase" }}>
            {c.type.replace(/_/g, " ")}
          </div>
          <div className="text-dim" style={{ fontSize: 12, marginTop: 4 }}>
            {c.detail}
          </div>
          {c.dominant_parent_id && (
            <div className="text-faint" style={{ fontSize: 11, marginTop: 2 }}>
              via {c.dominant_parent_id}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
