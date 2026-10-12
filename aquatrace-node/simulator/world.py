"""World Simulator: stands in for the physical drain network. Owns the synthetic DAG, per-node
diurnal contamination baselines, and per-edge transport (§3's closed-form Gaussian-slug kernel,
applied as a discrete convolution of each parent's own flux-history time series — so an injected
event at any node propagates to every descendant automatically, hop by hop, purely as an
emergent effect of each child convolving its parents' recent flux history; nothing here
special-cases "downstream of an injection").

Physics ticks are driven lazily: `tick()` recomputes every node's true concentration/hydraulics
for the whole DAG (parents before children, since child computation depends on parent flux
history that must already include this tick) whenever WORLD_TICK_INTERVAL_S of simulated time
has elapsed, triggered by whichever caller asks for a reading first after that boundary —
avoiding a second independent background loop alongside SimClock's.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional

import numpy as np

from registry import Registry, get_registry
from simulator.clock import SimClock
from simulator.physics import (
    convolve_parent_flux_to_child_instant,
    discharge,
    dispersion_coefficient,
    manning_level_from_discharge,
    manning_velocity,
)

# Real-world anchor: the Najafgarh drain -- Delhi's largest, carrying 60%+ of the city's
# wastewater into the Yamuna (per CPCB) -- and its actual confluence with the river, ~0.5km
# downstream of Wazirabad barrage (28.712448N 77.233184E). Node placement below samples a root
# point along this real corridor and biases each subsequent reach's bearing toward its real flow
# direction (upstream near Dhansa -> the Yamuna confluence), instead of scattering uniformly
# across the whole Delhi bounding box -- so the generated network visually traces a real, named
# waterway on the map rather than floating over arbitrary neighborhoods. The monitored network
# itself is still only a few hundred meters to ~1-2km end to end (REACH_LENGTH_RANGE_M below), a
# representative stretch of the drain, not the whole ~57km course -- same as a real deployed
# sensor fleet wouldn't cover an entire river system either.
DRAIN_UPSTREAM_LATLON = (28.535, 76.905)  # near Dhansa, the drain's source area within Delhi
YAMUNA_CONFLUENCE_LATLON = (28.708, 77.235)  # ~0.5km downstream of Wazirabad barrage


def _bearing_rad(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, in the same convention _offset_latlon
    expects (0 = north, increasing clockwise toward east)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return math.atan2(x, y)


DRAIN_FLOW_BEARING_RAD = _bearing_rad(*DRAIN_UPSTREAM_LATLON, *YAMUNA_CONFLUENCE_LATLON)

# Domain-reasonable nominal baseline concentrations (registry-consistent: kept below limit_safe
# for high_is_bad species, above limit_safe for low_is_bad, mid-band for pH). Not part of
# registry.json itself since the registry defines thresholds, not expected ambient values.
NOMINAL_BASELINE: dict[str, float] = {
    "ph": 7.6,
    "temperature": 24.0,
    "conductivity": 600.0,
    "tss": 25.0,
    "dissolved_oxygen": 5.5,
    "bod": 2.0,
    "cod": 50.0,
    "ammoniacal_n": 8.0,
    "fecal_coliform": 1500.0,
    "chromium_vi": 0.01,
    "urea": 3.0,
}

# Diurnal direction: +1 rises with the domestic-discharge peak hours, -1 falls (e.g. DO sag
# under higher organic load), fractional = muted response.
DIURNAL_DIRECTION: dict[str, float] = {
    "ph": 0.1,
    "temperature": 0.3,
    "conductivity": 1.0,
    "tss": 1.0,
    "dissolved_oxygen": -0.6,
    "bod": 1.0,
    "cod": 1.0,
    "ammoniacal_n": 1.0,
    "fecal_coliform": 1.0,
    "chromium_vi": 0.4,
    "urea": 1.0,
}

NOISE_FRACTION = 0.04
LOCAL_INFLOW_FRACTION = 0.15  # non-root nodes contribute this fraction of a full local baseline
# as lateral inflow; their remaining signal comes from convolving parents' flux forward (tick())
LOCAL_Q_INFLOW_FRACTION = 0.10  # matching lateral *flow* inflow fraction added to accumulated
# parent discharge at each non-root node (see _hydraulics_at)
WORLD_TICK_INTERVAL_S = 30.0
MIN_HISTORY_LOOKBACK_S = 3600.0
EARTH_RADIUS_M = 6_371_000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _offset_latlon(lat: float, lon: float, distance_m: float, bearing_rad: float) -> tuple[float, float]:
    dlat = (distance_m * math.cos(bearing_rad)) / EARTH_RADIUS_M
    dlon = (distance_m * math.sin(bearing_rad)) / (EARTH_RADIUS_M * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


REACH_LENGTH_RANGE_M = (150.0, 900.0)  # realistic spacing between adjacent monitoring points
CONFLUENCE_MAX_DISTANCE_M = 1500.0  # a second parent must be a genuinely nearby tributary


@dataclass
class NodeSpec:
    node_id: str
    latitude: float
    longitude: float
    breadth_m: float
    invert_level_m: float
    manning_n: float  # ground truth channel roughness; CC never observes this directly
    baseline_level_m: float


@dataclass
class EdgeSpec:
    parent_id: str
    child_id: str
    length_m: float
    slope: float  # ground truth bed slope; CC only ever estimates this


@dataclass
class InjectedEvent:
    node_id: str
    contaminant_id: str
    magnitude: float
    start_time: datetime
    duration_s: float
    onset_sharpness: float


@dataclass
class SensorFault:
    node_id: str
    contaminant_id: str
    fault_type: Literal["stuck", "dropout", "spike"]
    start_time: datetime
    duration_s: float


class WorldSimulator:
    def __init__(self, node_count: int, seed: int, clock: SimClock, registry: Optional[Registry] = None):
        self.clock = clock
        self.registry = registry or get_registry()
        self.rng = np.random.default_rng(seed)

        self.nodes: dict[str, NodeSpec] = {}
        self.edges: list[EdgeSpec] = []
        self.parents_of: dict[str, list[str]] = {}
        self.children_of: dict[str, list[str]] = {}
        self.topo_order: list[str] = []

        self._build_dag(node_count)

        history_len = max(
            int(self._max_tau_s() * 3 / WORLD_TICK_INTERVAL_S),
            int(MIN_HISTORY_LOOKBACK_S / WORLD_TICK_INTERVAL_S),
        ) + 4
        self._history_len = history_len

        transportable = self._transportable_ids()
        self.flux_history: dict[str, dict[str, deque]] = {
            n: {c: deque([0.0] * history_len, maxlen=history_len) for c in transportable} for n in self.nodes
        }
        self.current_concentration: dict[str, dict[str, float]] = {n: {} for n in self.nodes}
        self.current_hydraulics: dict[str, tuple[float, float, float]] = {n: (0.0, 0.0, 0.0) for n in self.nodes}
        self._last_good_value: dict[str, dict[str, float]] = {n: {} for n in self.nodes}

        self.events: list[InjectedEvent] = []
        self.faults: list[SensorFault] = []

        self._last_tick_time: Optional[datetime] = None
        self._warm_up(clock.now())

    def _warm_up(self, now: datetime) -> None:
        """Silently ticks the DAG through its own recent past before exposing `now()` to
        callers. Without this, flux_history starts all-zero, so every downstream node's
        convolution against its parents' history spends its first ~history_len ticks (~1-2 hours
        of simulated time) climbing from an artificially-empty history to true steady state —
        that climb is easy to mistake for a slow-decaying response to whatever happened to get
        injected early on, when it's really just the cold-start transient."""
        start = now - timedelta(seconds=self._history_len * WORLD_TICK_INTERVAL_S)
        steps = self._history_len
        for i in range(steps + 1):
            self.tick(start + timedelta(seconds=i * WORLD_TICK_INTERVAL_S))

    # ---- DAG construction ----

    def _build_dag(self, node_count: int) -> None:
        """Grows the network incrementally, each new node placed a short reach downstream of a
        parent already placed — rather than scattering all nodes uniformly across the whole
        bounding box and connecting whichever ones happen to be picked, which produces
        multi-kilometer "reaches" between adjacent monitoring points and wildly unrealistic
        travel times. A confluence's second parent is deliberately chosen from spatially nearby
        earlier nodes (a real tributary), not just an earlier index."""
        node_ids = [f"SIM-N{idx + 1:03d}" for idx in range(node_count)]

        def new_spec(node_id: str, i: int, lat: float, lon: float) -> NodeSpec:
            manning_n = float(self.rng.choice([self.rng.uniform(0.013, 0.016), self.rng.uniform(0.03, 0.06)]))
            # Channel size/depth trend upward with topological depth (a proxy for downstream
            # position, since creation order is a valid topo order) rather than being drawn
            # independently per node — a real drain's cross-section is sized for the flow it
            # accumulates, so Q should generally not decrease downstream. Fully independent
            # random geometry let Q randomly *drop* at some nodes, which forces concentration to
            # rise sharply there just to conserve mass flux (C = flux/Q) — not a transport
            # signal, just an artifact of ungrounded geometry.
            growth = min(i / 24.0, 1.0)
            breadth_m = max(2.0 + growth * 3.5 + float(self.rng.uniform(-0.3, 0.3)), 1.5)
            baseline_level_m = max(0.15 + growth * 0.25 + float(self.rng.uniform(-0.03, 0.03)), 0.08)
            return NodeSpec(
                node_id=node_id,
                latitude=lat,
                longitude=lon,
                breadth_m=breadth_m,
                invert_level_m=215.0 - i * float(self.rng.uniform(0.6, 1.8)),
                manning_n=manning_n,
                baseline_level_m=baseline_level_m,
            )

        root_id = node_ids[0]
        # Root sits somewhere in the upstream-to-mid stretch of the real corridor (not right at
        # the confluence), so there's real corridor left downstream for children to trace toward
        # the Yamuna. Small perpendicular-ish jitter keeps different seeds visibly different
        # without losing the anchor to the real waterway.
        corridor_frac = float(self.rng.uniform(0.05, 0.55))
        root_lat = DRAIN_UPSTREAM_LATLON[0] + corridor_frac * (YAMUNA_CONFLUENCE_LATLON[0] - DRAIN_UPSTREAM_LATLON[0])
        root_lon = DRAIN_UPSTREAM_LATLON[1] + corridor_frac * (YAMUNA_CONFLUENCE_LATLON[1] - DRAIN_UPSTREAM_LATLON[1])
        root_lat += float(self.rng.uniform(-0.01, 0.01))
        root_lon += float(self.rng.uniform(-0.01, 0.01))
        self.nodes[root_id] = new_spec(root_id, 0, root_lat, root_lon)
        self.parents_of[root_id] = []
        self.children_of[root_id] = []

        for i in range(1, len(node_ids)):
            child_id = node_ids[i]
            candidates = node_ids[:i]
            weights = np.array([1.0 / (i - j) for j in range(len(candidates))])
            weights /= weights.sum()
            primary_id = candidates[int(self.rng.choice(len(candidates), p=weights))]
            primary = self.nodes[primary_id]

            distance_m = float(self.rng.uniform(*REACH_LENGTH_RANGE_M))
            # Biased toward the real drain's actual flow direction (upstream -> Yamuna
            # confluence) rather than uniformly random, so the network trends the right way on
            # the map instead of wandering in arbitrary directions; the spread still allows
            # realistic branch/tributary angles off the main trend.
            bearing = DRAIN_FLOW_BEARING_RAD + float(self.rng.normal(0, math.radians(35)))
            child_lat, child_lon = _offset_latlon(primary.latitude, primary.longitude, distance_m, bearing)

            self.nodes[child_id] = new_spec(child_id, i, child_lat, child_lon)
            self.parents_of[child_id] = []
            self.children_of[child_id] = []

            parent_ids = [primary_id]
            if i >= 2 and self.rng.random() < 0.18:
                others = [c for c in candidates if c != primary_id]
                dists = np.array([_haversine_m(child_lat, child_lon, self.nodes[c].latitude, self.nodes[c].longitude) for c in others])
                close = dists < CONFLUENCE_MAX_DISTANCE_M
                if np.any(close):
                    close_ids = [c for c, m in zip(others, close) if m]
                    close_dists = dists[close]
                    w2 = 1.0 / (close_dists + 1.0)
                    w2 /= w2.sum()
                    parent_ids.append(close_ids[int(self.rng.choice(len(close_ids), p=w2))])

            for parent_id in parent_ids:
                parent, child = self.nodes[parent_id], self.nodes[child_id]
                length_m = max(_haversine_m(parent.latitude, parent.longitude, child.latitude, child.longitude), 20.0)
                slope = max((parent.invert_level_m - child.invert_level_m) / length_m, 0.0003)
                self.edges.append(EdgeSpec(parent_id, child_id, length_m, slope))
                self.parents_of[child_id].append(parent_id)
                self.children_of[parent_id].append(child_id)

        # node_ids creation order is already a valid topological order: every edge's parent
        # index is strictly less than its child's (candidates are always earlier-created nodes)
        self.topo_order = list(node_ids)

    def _edge(self, parent_id: str, child_id: str) -> EdgeSpec:
        for e in self.edges:
            if e.parent_id == parent_id and e.child_id == child_id:
                return e
        raise KeyError((parent_id, child_id))

    def _max_tau_s(self) -> float:
        taus = []
        for e in self.edges:
            child = self.nodes[e.child_id]
            v = max(manning_velocity(child.baseline_level_m, child.breadth_m, child.manning_n, e.slope), 0.05)
            taus.append(e.length_m / v)
        return max(taus) if taus else MIN_HISTORY_LOOKBACK_S / 3

    def _transportable_ids(self) -> list[str]:
        # ph/temperature are intrinsic water-body properties, not transported pollutant mass
        return [c for c in self.registry.all_ids() if c not in ("ph", "temperature")]

    # ---- generators ----

    def _diurnal_multiplier(self, t: datetime, direction: float) -> float:
        hour = t.hour + t.minute / 60.0 + t.second / 3600.0
        morning = math.exp(-((hour - 8.0) ** 2) / (2 * 1.4**2))
        evening = math.exp(-((hour - 19.0) ** 2) / (2 * 1.6**2))
        hump = 0.35 * (morning + evening)
        weekend_mult = 0.82 if t.weekday() >= 5 else 1.0
        return 1.0 + direction * hump * weekend_mult

    def _hydraulics_at(self, node_id: str, t: datetime) -> tuple[float, float, float]:
        """Root (parentless) nodes get level from the diurnal generator and derive Q from it via
        Manning's, as the only physically meaningful source of signal they have. Non-root nodes
        do the reverse: Q is accumulated from parents (already computed earlier this tick, since
        topo_order guarantees parents precede children) plus a small local lateral-inflow
        fraction, and level/speed are derived FROM that Q via the inverted Manning's equation —
        so Q is guaranteed monotonically non-decreasing downstream, matching how a real drain's
        cross-section is sized for the flow it accumulates. Sampling level independently at every
        node (the original approach) let Q randomly *drop* at some nodes purely from unrelated
        random geometry, which forces concentration to spike there just to conserve mass flux
        (C = flux/Q) — an artifact, not a transport signal."""
        spec = self.nodes[node_id]
        out_edges = [e for e in self.edges if e.parent_id == node_id]
        slope = out_edges[0].slope if out_edges else 0.002
        parents = self.parents_of[node_id]

        if not parents:
            level = max(spec.baseline_level_m * self._diurnal_multiplier(t, direction=0.5), 0.03)
            v = manning_velocity(level, spec.breadth_m, spec.manning_n, slope)
            q = discharge(v, spec.breadth_m, level)
            return level, v, q

        q_in = sum(self.current_hydraulics[p][2] for p in parents)
        q = q_in * (1.0 + LOCAL_Q_INFLOW_FRACTION * self._diurnal_multiplier(t, direction=0.4))
        level = max(manning_level_from_discharge(q, spec.breadth_m, spec.manning_n, slope), 0.03)
        v = manning_velocity(level, spec.breadth_m, spec.manning_n, slope)
        return level, v, q

    def _baseline_concentration(self, contaminant_id: str, t: datetime, fraction: float = 1.0) -> float:
        """`fraction` distinguishes a headwater's full local baseline (fraction=1, it's the only
        source of signal it has) from a downstream node's *lateral* inflow (fraction=
        LOCAL_INFLOW_FRACTION): a downstream node's total signal is this small local
        contribution plus whatever its parents' flux convolves forward (see tick()) — using
        fraction=1 at every node would double-count the same baseline load at every hop, since
        the parent's full baseline is already present in the parent's flux history and gets
        carried forward by the convolution."""
        nominal = NOMINAL_BASELINE.get(contaminant_id, 1.0)
        direction = DIURNAL_DIRECTION.get(contaminant_id, 0.0)
        mult = self._diurnal_multiplier(t, direction)
        noise = float(self.rng.normal(0, NOISE_FRACTION))
        return max(nominal * mult * (1 + noise) * fraction, 0.0)

    def _event_overlay(self, node_id: str, contaminant_id: str, t: datetime) -> float:
        total = 0.0
        for ev in self.events:
            if ev.node_id != node_id or ev.contaminant_id != contaminant_id:
                continue
            elapsed = (t - ev.start_time).total_seconds()
            if elapsed < 0 or elapsed > ev.duration_s:
                continue
            phase = elapsed / ev.duration_s
            shape = math.sin(math.pi * phase) ** max(ev.onset_sharpness, 0.2)
            total += ev.magnitude * shape
        return total

    def _apply_fault(self, node_id: str, contaminant_id: str, t: datetime, true_value: float) -> float:
        for f in self.faults:
            if f.node_id != node_id or f.contaminant_id != contaminant_id:
                continue
            elapsed = (t - f.start_time).total_seconds()
            if elapsed < 0 or elapsed > f.duration_s:
                continue
            if f.fault_type == "stuck":
                return self._last_good_value.get(node_id, {}).get(contaminant_id, true_value)
            if f.fault_type == "dropout":
                return 0.0
            if f.fault_type == "spike":
                return true_value * 8.0 + NOMINAL_BASELINE.get(contaminant_id, 1.0) * 5.0
        self._last_good_value.setdefault(node_id, {})[contaminant_id] = true_value
        return true_value

    # ---- tick / propagation ----

    def tick(self, t: datetime) -> None:
        if self._last_tick_time is not None and (t - self._last_tick_time).total_seconds() < WORLD_TICK_INTERVAL_S:
            return
        self._last_tick_time = t

        for node_id in self.topo_order:
            level, v, q = self._hydraulics_at(node_id, t)
            self.current_hydraulics[node_id] = (level, v, q)
            area = max(self.nodes[node_id].breadth_m * level, 0.01)
            is_root = len(self.parents_of[node_id]) == 0
            baseline_fraction = 1.0 if is_root else LOCAL_INFLOW_FRACTION

            for contaminant_id in self.registry.all_ids():
                if contaminant_id in ("ph", "temperature"):
                    # intrinsic water-body properties: always locally generated in full, never
                    # carried forward by convolution (see _transportable_ids)
                    value = self._baseline_concentration(contaminant_id, t, fraction=1.0)
                    value += self._event_overlay(node_id, contaminant_id, t)
                    self.current_concentration[node_id][contaminant_id] = value
                    continue

                value = self._baseline_concentration(contaminant_id, t, fraction=baseline_fraction)
                value += self._event_overlay(node_id, contaminant_id, t)

                contam = self.registry[contaminant_id]
                k = contam.decay_rate_per_s
                for parent_id in self.parents_of[node_id]:
                    edge = self._edge(parent_id, node_id)
                    parent_flux = np.array(self.flux_history[parent_id][contaminant_id])
                    if not np.any(parent_flux):
                        continue
                    d_coef = dispersion_coefficient(v, self.nodes[node_id].breadth_m, level, edge.slope)
                    value += convolve_parent_flux_to_child_instant(
                        parent_flux, WORLD_TICK_INTERVAL_S, edge.length_m, d_coef, max(v, 0.05), k, area
                    )

                value = max(value, 0.0)
                self.current_concentration[node_id][contaminant_id] = value
                self.flux_history[node_id][contaminant_id].append(value * q)

    # ---- public query API (used by core/virtual_node.py) ----

    def get_reading(self, node_id: str, contaminant_id: str, t: datetime) -> float:
        self.tick(t)
        true_value = self.current_concentration[node_id].get(contaminant_id, 0.0)
        return self._apply_fault(node_id, contaminant_id, t, true_value)

    def get_hydraulics(self, node_id: str, t: datetime) -> tuple[float, float, float]:
        self.tick(t)
        return self.current_hydraulics[node_id]

    def sensor_ids_for(self, node_id: str) -> list[str]:
        return self.registry.live_stream_ids()

    def node_ids(self) -> list[str]:
        return list(self.topo_order)

    def claimed_parent_ids(self, node_id: str) -> list[str]:
        return list(self.parents_of.get(node_id, []))

    def node_spec(self, node_id: str) -> NodeSpec:
        return self.nodes[node_id]

    # ---- injection API (§5.1a) ----

    def inject_event(
        self,
        node_id: str,
        contaminant_id: str,
        magnitude: float,
        start_time: datetime,
        duration_s: float,
        onset_sharpness: float = 1.0,
    ) -> None:
        if node_id not in self.nodes:
            raise ValueError(f"unknown node_id {node_id}")
        self.events.append(InjectedEvent(node_id, contaminant_id, magnitude, start_time, duration_s, onset_sharpness))

    def inject_sensor_fault(
        self,
        node_id: str,
        contaminant_id: str,
        fault_type: Literal["stuck", "dropout", "spike"],
        start_time: datetime,
        duration_s: float,
    ) -> None:
        if node_id not in self.nodes:
            raise ValueError(f"unknown node_id {node_id}")
        self.faults.append(SensorFault(node_id, contaminant_id, fault_type, start_time, duration_s))
