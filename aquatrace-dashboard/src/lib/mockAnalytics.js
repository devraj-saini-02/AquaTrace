// Everything in this module is synthetic, client-side-only illustrative data — the Analysis
// Dashboard deliberately never calls central-system. Real deployments only retain
// RETENTION_HOURS (12h default, see central-system/README §Notable design points) of raw
// summaries, so there is no real backend endpoint that could ever serve a genuine 1-year trend;
// this exists purely to show what that view would look like once real long-horizon storage
// exists. Contaminant thresholds are duplicated from central-system/config/registry.json (that
// file is itself already duplicated verbatim between node/ and central-system/, so a third,
// frontend-only copy of just the display-relevant fields follows the same established pattern).

export const CONTAMINANTS = [
  { id: "bod", label: "BOD", unit: "mg/L", polarity: "high_is_bad", safe: 3.0, warn: 30.0, baseline: 14, monsoonAmp: 10, noise: 2.2 },
  { id: "cod", label: "COD", unit: "mg/L", polarity: "high_is_bad", safe: 80.0, warn: 250.0, baseline: 150, monsoonAmp: 70, noise: 14 },
  { id: "ammoniacal_n", label: "Ammoniacal N", unit: "mg/L", polarity: "high_is_bad", safe: 15.0, warn: 50.0, baseline: 24, monsoonAmp: 14, noise: 3.2 },
  { id: "tss", label: "TSS", unit: "mg/L", polarity: "high_is_bad", safe: 40.0, warn: 100.0, baseline: 34, monsoonAmp: 55, noise: 6 },
  { id: "dissolved_oxygen", label: "Dissolved Oxygen", unit: "mg/L", polarity: "low_is_bad", safe: 5.0, warn: 4.0, baseline: 6.1, monsoonAmp: -1.6, noise: 0.35 },
  { id: "chromium_vi", label: "Chromium VI", unit: "mg/L", polarity: "high_is_bad", safe: 0.03, warn: 0.1, baseline: 0.018, monsoonAmp: 0.006, noise: 0.006 },
  { id: "ph", label: "pH", unit: "pH", polarity: "band", safeLow: 6.5, safeHigh: 8.5, warnLow: 5.5, warnHigh: 9.0, baseline: 7.3, monsoonAmp: -0.3, noise: 0.22 },
  { id: "conductivity", label: "Conductivity", unit: "µS/cm", polarity: "context", baseline: 820, monsoonAmp: -180, noise: 40 },
  { id: "temperature", label: "Temperature", unit: "°C", polarity: "context", baseline: 24, monsoonAmp: 0, noise: 1.1, seasonalOverride: "temperature" },
  { id: "urea", label: "Urea", unit: "mg/L", polarity: "context", baseline: 21, monsoonAmp: 6, noise: 2.5 },
];

export function classify(c, value) {
  if (c.polarity === "context") return null;
  if (c.polarity === "band") {
    if (value >= c.safeLow && value <= c.safeHigh) return "SAFE";
    if (value >= c.warnLow && value <= c.warnHigh) return "WARN";
    return "RED";
  }
  if (c.polarity === "low_is_bad") {
    if (value >= c.safe) return "SAFE";
    if (value >= c.warn) return "WARN";
    return "RED";
  }
  // high_is_bad
  if (value <= c.safe) return "SAFE";
  if (value <= c.warn) return "WARN";
  return "RED";
}

// mulberry32: tiny deterministic PRNG so the "historical" data is stable across re-renders
// instead of jittering every poll cycle.
function mulberry32(seed) {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(rand) {
  const u = Math.max(rand(), 1e-9);
  const v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

// Raised-cosine bump centered on day-of-year `center`, full width `width` -- models Delhi's
// monsoon-season pollutant loading (roughly June-September) without a hard step.
function seasonalBump(dayOfYear, center, width) {
  const d = Math.abs(((dayOfYear - center + 365.5) % 365) - 182.5);
  if (d > width / 2) return 0;
  return 0.5 * (1 + Math.cos((2 * Math.PI * d) / width));
}

function seasonalFactor(c, dayOfYear) {
  if (c.seasonalOverride === "temperature") {
    // Smooth annual cycle peaking pre-monsoon (~day 155, late May) and troughing midwinter.
    return Math.sin((2 * Math.PI * (dayOfYear - 65)) / 365);
  }
  return seasonalBump(dayOfYear, 200, 120);
}

const DAY_MS = 86400000;

export function generateYearSeries(contaminantId, endDate = new Date()) {
  const c = CONTAMINANTS.find((x) => x.id === contaminantId);
  if (!c) return [];
  const rand = mulberry32(hashString(contaminantId));
  const points = [];
  for (let i = 364; i >= 0; i--) {
    const date = new Date(endDate.getTime() - i * DAY_MS);
    const dayOfYear = Math.floor((date - new Date(date.getFullYear(), 0, 0)) / DAY_MS);
    const seasonal = seasonalFactor(c, dayOfYear);
    let value = c.baseline + c.monsoonAmp * seasonal + gaussian(rand) * c.noise;
    // A handful of sharp, short-lived discharge events per year for visual/narrative interest.
    if (rand() < 0.012) value += (c.monsoonAmp !== 0 ? Math.abs(c.monsoonAmp) : c.baseline) * (1.5 + rand());
    if (c.polarity !== "context" && (c.safe ?? c.safeLow) > 0 && value < 0) value = Math.abs(value) * 0.1;
    points.push({ date: date.toISOString().slice(0, 10), t: date.getTime(), value });
  }
  return points;
}

function hashString(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return h;
}

export function seriesStats(c, points) {
  const n = points.length || 1;
  let safe = 0,
    warn = 0,
    red = 0,
    sum = 0,
    min = Infinity,
    max = -Infinity;
  for (const p of points) {
    sum += p.value;
    if (p.value < min) min = p.value;
    if (p.value > max) max = p.value;
    const state = classify(c, p.value);
    if (state === "SAFE") safe++;
    else if (state === "WARN") warn++;
    else if (state === "RED") red++;
  }
  return {
    avg: sum / n,
    min,
    max,
    safePct: Math.round((100 * safe) / n),
    warnPct: Math.round((100 * warn) / n),
    redPct: Math.round((100 * red) / n),
  };
}

// Generates a reading that actually falls in the `status` bucket per classify()'s own rules --
// each polarity (high_is_bad / low_is_bad / band) has a different direction for what "worse"
// means, so this can't be one formula.
function sampleReading(c, status, rand) {
  if (c.polarity === "band") {
    if (status === "SAFE") return (c.safeLow + c.safeHigh) / 2 + (rand() - 0.5) * (c.safeHigh - c.safeLow) * 0.8;
    if (status === "WARN")
      return rand() < 0.5 ? c.safeLow - rand() * (c.safeLow - c.warnLow) * 0.8 : c.safeHigh + rand() * (c.warnHigh - c.safeHigh) * 0.8;
    return rand() < 0.5 ? c.warnLow - rand() * c.warnLow * 0.3 : c.warnHigh + rand() * c.warnHigh * 0.3;
  }
  if (c.polarity === "low_is_bad") {
    if (status === "SAFE") return c.safe * (1.05 + rand() * 0.3);
    if (status === "WARN") return c.warn + rand() * (c.safe - c.warn) * 0.9;
    return c.warn * (0.3 + rand() * 0.6);
  }
  // high_is_bad
  if (status === "SAFE") return c.safe * (0.3 + rand() * 0.6);
  if (status === "WARN") return c.safe + rand() * (c.warn - c.safe) * 0.9;
  return c.warn * (1.1 + rand() * 0.6);
}

// A fixed, deterministic fleet snapshot -- this dashboard never calls central-system, so the
// node roster mirrors the default demo simulation's naming (SIM-N001..) without depending on one
// actually running.
export function mockFleetStatus() {
  const rand = mulberry32(1337);
  const statuses = ["SAFE", "SAFE", "SAFE", "SAFE", "SAFE", "SAFE", "SAFE", "SAFE", "WARN", "WARN", "RED", "OFFLINE"];
  const contaminantsById = Object.fromEntries(CONTAMINANTS.filter((c) => c.polarity !== "context").map((c) => [c.id, c]));
  const pollutants = Object.keys(contaminantsById);
  const nodeCount = 15;
  // Fixed seed can, by chance, draw zero RED/OFFLINE across the whole fleet -- force a few
  // specific slots so the full status palette is always actually visible, not just probable.
  const forced = { 5: "WARN", 9: "RED", 10: "RED", 13: "OFFLINE" };
  return Array.from({ length: nodeCount }, (_, i) => {
    const id = `SIM-N${String(i + 1).padStart(3, "0")}`;
    const status = forced[i] || statuses[Math.floor(rand() * statuses.length)];
    const contaminantId = pollutants[Math.floor(rand() * pollutants.length)];
    const c = contaminantsById[contaminantId];
    const reading = status === "OFFLINE" ? 0 : sampleReading(c, status, rand);
    return { nodeId: id, status, contaminantId, contaminantLabel: c.label, reading, unit: c.unit };
  });
}
