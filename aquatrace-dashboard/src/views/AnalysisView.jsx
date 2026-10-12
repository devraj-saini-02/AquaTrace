import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import StatusPill from "../components/StatusPill";
import { colorForStatus } from "../components/status";
import { CONTAMINANTS, classify, generateYearSeries, mockFleetStatus, seriesStats } from "../lib/mockAnalytics";

// Everything on this view is generated client-side (see lib/mockAnalytics.js) -- it never calls
// central-system. It exists to show what long-horizon trend analysis would look like once real
// storage beyond the 12h retention window exists; see the module doc comment for why that data
// can't come from the real backend today.

function zonesFor(c, yMin, yMax) {
  const safeColor = "var(--safe)";
  const warnColor = "var(--warn)";
  const redColor = "var(--red)";
  if (c.polarity === "context") return [];
  if (c.polarity === "band") {
    return [
      { y1: yMin, y2: c.warnLow, color: redColor },
      { y1: c.warnLow, y2: c.safeLow, color: warnColor },
      { y1: c.safeLow, y2: c.safeHigh, color: safeColor },
      { y1: c.safeHigh, y2: c.warnHigh, color: warnColor },
      { y1: c.warnHigh, y2: yMax, color: redColor },
    ];
  }
  if (c.polarity === "low_is_bad") {
    return [
      { y1: yMin, y2: c.warn, color: redColor },
      { y1: c.warn, y2: c.safe, color: warnColor },
      { y1: c.safe, y2: yMax, color: safeColor },
    ];
  }
  return [
    { y1: yMin, y2: c.safe, color: safeColor },
    { y1: c.safe, y2: c.warn, color: warnColor },
    { y1: c.warn, y2: yMax, color: redColor },
  ];
}

export default function AnalysisView() {
  const [selectedId, setSelectedId] = useState(CONTAMINANTS[0].id);
  const fleet = useMemo(() => mockFleetStatus(), []);
  const allSeries = useMemo(() => Object.fromEntries(CONTAMINANTS.map((c) => [c.id, generateYearSeries(c.id)])), []);

  const selected = CONTAMINANTS.find((c) => c.id === selectedId);
  const selectedSeries = allSeries[selectedId];
  const stats = useMemo(() => seriesStats(selected, selectedSeries), [selected, selectedSeries]);

  const counts = fleet.reduce((acc, n) => ({ ...acc, [n.status]: (acc[n.status] || 0) + 1 }), {});

  return (
    <div>
      <div className="view-header">
        <h2>Analysis Dashboard</h2>
        <span className="text-dim">trailing 12 months</span>
      </div>
      <div className="text-faint" style={{ fontSize: 12, marginBottom: 16 }}>
        Illustrative long-horizon trends, generated client-side — central-system retains only a 12h rolling window (see
        README §Notable design points), so this view never calls the backend.
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          <strong style={{ fontSize: 13 }}>Fleet Status</strong>
          <div className="row" style={{ gap: 14, fontSize: 12 }}>
            <CountBadge status="SAFE" count={counts.SAFE || 0} />
            <CountBadge status="WARN" count={counts.WARN || 0} />
            <CountBadge status="RED" count={counts.RED || 0} />
            <CountBadge status="OFFLINE" count={counts.OFFLINE || 0} label="offline" />
          </div>
        </div>
        <div className="node-status-grid">
          {fleet.map((n) => (
            <div key={n.nodeId} className="node-status-card" style={{ borderLeftColor: colorForStatus(n.status) }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="mono" style={{ fontSize: 12 }}>
                  {n.nodeId}
                </span>
                <span className="status-dot" style={{ background: colorForStatus(n.status) }} />
              </div>
              <div className="text-faint" style={{ fontSize: 11, marginTop: 4 }}>
                {n.status === "OFFLINE" ? "no signal" : `${n.contaminantLabel} ${n.reading.toFixed(2)} ${n.unit}`}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <strong style={{ fontSize: 13, display: "block", marginBottom: 12 }}>Contaminant Levels — select to expand</strong>
        <div className="sparkline-grid">
          {CONTAMINANTS.map((c) => {
            const series = allSeries[c.id];
            const last = series[series.length - 1];
            const state = classify(c, last.value);
            return (
              <button
                key={c.id}
                className={`sparkline-card ${selectedId === c.id ? "selected" : ""}`}
                onClick={() => setSelectedId(c.id)}
              >
                <div className="row" style={{ justifyContent: "space-between", marginBottom: 2 }}>
                  <span style={{ fontSize: 12, fontWeight: 600 }}>{c.label}</span>
                  {state && <StatusPill status={state} />}
                </div>
                <div className="text-dim mono" style={{ fontSize: 11, marginBottom: 4 }}>
                  {last.value.toFixed(c.unit === "pH" ? 2 : c.unit === "mg/L" && c.id === "chromium_vi" ? 3 : 1)} {c.unit}
                </div>
                <ResponsiveContainer width="100%" height={36}>
                  <LineChart data={series}>
                    <Line type="monotone" dataKey="value" stroke="var(--accent)" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </button>
            );
          })}
        </div>
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
          <strong style={{ fontSize: 13 }}>
            {selected.label} — 365 days ({selected.unit})
          </strong>
          <span className="text-dim" style={{ fontSize: 12 }}>
            avg {stats.avg.toFixed(2)} · min {stats.min.toFixed(2)} · max {stats.max.toFixed(2)}
          </span>
        </div>
        {selected.polarity !== "context" && (
          <div className="row" style={{ gap: 16, marginBottom: 10, fontSize: 12 }}>
            <span style={{ color: "var(--safe)" }}>{stats.safePct}% safe</span>
            <span style={{ color: "var(--warn)" }}>{stats.warnPct}% warn</span>
            <span style={{ color: "var(--red)" }}>{stats.redPct}% red</span>
          </div>
        )}
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={selectedSeries} margin={{ left: -10, right: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            {zonesFor(selected, stats.min - (stats.max - stats.min) * 0.15, stats.max + (stats.max - stats.min) * 0.15).map((z, i) => (
              <ReferenceArea key={i} y1={z.y1} y2={z.y2} fill={z.color} fillOpacity={0.08} strokeOpacity={0} />
            ))}
            <XAxis
              dataKey="t"
              type="number"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, { month: "short" })}
              stroke="var(--text-faint)"
              fontSize={11}
            />
            <YAxis stroke="var(--text-faint)" fontSize={11} width={50} domain={["auto", "auto"]} />
            <ChartTooltip
              contentStyle={{ background: "var(--bg-panel)", border: "1px solid var(--border)", fontSize: 12 }}
              labelFormatter={(v) => new Date(v).toLocaleDateString()}
              formatter={(value) => [`${value.toFixed(2)} ${selected.unit}`, selected.label]}
            />
            {selected.polarity === "band" && (
              <>
                <ReferenceLine y={selected.safeLow} stroke="var(--safe)" strokeDasharray="3 3" strokeOpacity={0.5} />
                <ReferenceLine y={selected.safeHigh} stroke="var(--safe)" strokeDasharray="3 3" strokeOpacity={0.5} />
              </>
            )}
            {(selected.polarity === "high_is_bad" || selected.polarity === "low_is_bad") && (
              <>
                <ReferenceLine y={selected.safe} stroke="var(--safe)" strokeDasharray="3 3" strokeOpacity={0.5} />
                <ReferenceLine y={selected.warn} stroke="var(--red)" strokeDasharray="3 3" strokeOpacity={0.5} />
              </>
            )}
            <Line type="monotone" dataKey="value" stroke="var(--accent)" dot={false} strokeWidth={2} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
        {selected.polarity === "context" && (
          <div className="text-faint" style={{ fontSize: 11, marginTop: 6 }}>
            context signal — no fixed safe/warn threshold in the registry (used as a tracer / age-clock, not classified).
          </div>
        )}
      </div>
    </div>
  );
}

function CountBadge({ status, count, label }) {
  return (
    <span className="row" style={{ gap: 5 }}>
      <span className="status-dot" style={{ background: colorForStatus(status) }} />
      {count} {label || status.toLowerCase()}
    </span>
  );
}
