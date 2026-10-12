"""Tracing engine (§4.4) — the core of the whole system: reconciles each carrying node's
observed mass against what its parents' mass, shifted by travel time and decayed, would predict,
and flags nodes where the residual (unexplained mass U) is too large to be measurement noise.

Implements the pseudocode in the spec directly:

    def run_tracing(contaminant_id, time_window):
        order = topological_sort(nodes_carrying(contaminant_id))
        for node in order:
            M_observed = get_mass(node, contaminant_id, time_window)
            M_expected = sum(get_mass(parent, contaminant_id, shift(window, -tau)) * exp(-k*tau)
                              for parent in parents(node))
            U = M_observed - M_expected
            sigma_U = propagate_uncertainty(node, parents)
            if abs(U) > 2 * sigma_U:
                record_incident_candidate(node, classify_cause(...), confidence=f(U, sigma_U))

ph/temperature/conductivity carry no mass_g (see node/simulator/world.py and
core/virtual_node.py for why) so they're skipped — there is nothing for a mass-balance engine to
reconcile for them. Conductivity's designated role (primary_travel_time_tracer) is a natural
future refinement of current_tau() via cross-correlation of its concentration signal, not
implemented here since the given pseudocode is entirely mass-based.

propagate_uncertainty(node, parents) from the pseudocode is inlined into run_tracing's main loop
rather than kept as a separate call, since computing it needs the same per-parent shifted-window
mass query that M_expected's accumulation already issues -- a separate function would just repeat
that query. Headwater nodes (no parents) get a documented fallback there: M_expected=0 would make
every normal reading "anomalous" (nothing to explain any of it), so they're compared against
their own immediately-preceding window instead -- a legitimate secondary detection mode
(rolling-baseline anomaly detection), not junction mass balance, used only where mass balance is
undefined.
"""
import logging
import math
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models import ContaminantReadingRow, Edge, Incident, IncidentNode, IncidentStatus, SummaryWindow
from app.registry import get_registry

logger = logging.getLogger(__name__)

DEFAULT_MIN_WINDOW_S = 3600.0
NON_MASS_CONTAMINANTS = ("ph", "temperature", "conductivity")
UNCERTAINTY_FLOOR_G = 1e-3
EXPLAINED_FRACTION = 0.6  # M_expected covering at least this fraction of M_observed -> pass-through territory
NEGLIGIBLE_FRACTION = 0.2  # M_expected below this fraction of M_observed -> explains ~nothing -> local injection
DILUTION_Q_DROP_FRACTION = 0.7  # child Q below this fraction of parent Q suggests dilution loss


async def compute_tracing_window_s(session: AsyncSession) -> float:
    """§4.2: adaptive, >= 2 * max(tau_base_s across all edges). Recomputed each call — cheap
    query, and topology can change as new nodes register."""
    max_tau = (await session.execute(select(func.max(Edge.tau_base_s)))).scalar()
    if not max_tau:
        return DEFAULT_MIN_WINDOW_S
    return max(2.0 * max_tau, DEFAULT_MIN_WINDOW_S)


def _topological_sort(node_ids: set[str], edges: list[Edge]) -> list[str]:
    in_degree = {n: 0 for n in node_ids}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.parent_node_id in node_ids and e.child_node_id in node_ids:
            adjacency[e.parent_node_id].append(e.child_node_id)
            in_degree[e.child_node_id] += 1

    queue = deque(n for n in node_ids if in_degree[n] == 0)
    order: list[str] = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for m in adjacency[n]:
            in_degree[m] -= 1
            if in_degree[m] == 0:
                queue.append(m)
    order.extend(n for n in node_ids if n not in order)  # cycle fallback; shouldn't occur for a DAG
    return order


async def _nodes_carrying(session: AsyncSession, contaminant_id: str) -> set[str]:
    rows = (
        await session.execute(
            select(SummaryWindow.node_id)
            .join(ContaminantReadingRow, ContaminantReadingRow.summary_id == SummaryWindow.id)
            .where(ContaminantReadingRow.contaminant_id == contaminant_id)
            .distinct()
        )
    ).scalars().all()
    return set(rows)


async def _edges_among(session: AsyncSession, node_ids: set[str]) -> list[Edge]:
    if not node_ids:
        return []
    rows = (
        await session.execute(
            select(Edge).where(Edge.parent_node_id.in_(node_ids), Edge.child_node_id.in_(node_ids))
        )
    ).scalars().all()
    return list(rows)


async def _has_coverage(session: AsyncSession, node_id: str, contaminant_id: str, t_start: datetime, t_end: datetime) -> bool:
    """Whether at least one *trustworthy* (sensor_health="OK") reading falls in this window —
    distinct from get_mass() returning 0.0, which is indistinguishable between "genuinely
    near-zero", "no data here at all" (e.g. the window reaches back before this node existed),
    and "every reading in this window is FAILED/SUSPECT" (§10: a faulted sensor's corrupted
    values must not silently masquerade as a trustworthy zero). Callers that treat 0 as a
    meaningful expectation (the headwater baseline fallback in run_tracing) need this guard, or
    a brand-new node's very first reading — or a node mid-fault — looks like it exceeds
    "expected" by an infinite margin."""
    count = (
        await session.execute(
            select(func.count())
            .select_from(ContaminantReadingRow)
            .join(SummaryWindow, ContaminantReadingRow.summary_id == SummaryWindow.id)
            .where(
                SummaryWindow.node_id == node_id,
                ContaminantReadingRow.contaminant_id == contaminant_id,
                ContaminantReadingRow.sensor_health == "OK",
                SummaryWindow.t_end > t_start,
                SummaryWindow.t_end <= t_end,
            )
        )
    ).scalar()
    return bool(count)


async def get_mass(session: AsyncSession, node_id: str, contaminant_id: str, t_start: datetime, t_end: datetime) -> float:
    """Sums mass_g for readings with sensor_health="OK" only — a FAILED/SUSPECT reading (stuck,
    dropped out, or spiked, see core/health.py) reports a corrupted value, and trusting it in a
    mass-balance sum would turn a broken sensor into a phantom contamination event or a phantom
    "unexplained loss". Excluding it entirely (rather than down-weighting) is deliberate:
    _has_coverage() is what tells callers when a window has too little trustworthy data to
    evaluate at all, so a silently-lower sum here doesn't get misread as "less mass arrived"."""
    result = await session.execute(
        select(func.coalesce(func.sum(ContaminantReadingRow.mass_g), 0.0))
        .select_from(ContaminantReadingRow)
        .join(SummaryWindow, ContaminantReadingRow.summary_id == SummaryWindow.id)
        .where(
            SummaryWindow.node_id == node_id,
            ContaminantReadingRow.contaminant_id == contaminant_id,
            ContaminantReadingRow.sensor_health == "OK",
            SummaryWindow.t_end > t_start,
            SummaryWindow.t_end <= t_end,
        )
    )
    return float(result.scalar() or 0.0)


SENSOR_NOISE_FRACTION = 0.10  # instrument-precision floor for the mass uncertainty estimate;
# wide enough to absorb normal diurnal swings (up to ~35% amplitude, see simulator/world.py)
# without masking a genuine event (TSS validation: a true injected event's z-score was ~16 at
# this setting, an order of magnitude past the trigger)


async def _mass_variance(session: AsyncSession, node_id: str, contaminant_id: str, t_start: datetime, t_end: datetime) -> float:
    """Approximates mass uncertainty as a fixed fraction of the observed mass — the "sensor/flow
    error margins" the spec calls for, without a separate error-budget model. Deliberately NOT
    derived from each reading's own (mean, std): that within-cycle std reflects genuine signal
    dynamics (the true concentration changing fast during a transient event), not measurement
    error, and scaling variance by it made sigma_U balloon exactly for the biggest, most
    important anomalies -- self-defeating the very detector it was supposed to calibrate."""
    mass = await get_mass(session, node_id, contaminant_id, t_start, t_end)
    return (mass * SENSOR_NOISE_FRACTION) ** 2


async def _latest_summary(session: AsyncSession, node_id: str) -> Optional[SummaryWindow]:
    return (
        await session.execute(
            select(SummaryWindow).where(SummaryWindow.node_id == node_id).order_by(SummaryWindow.t_end.desc()).limit(1)
        )
    ).scalar_one_or_none()


async def _latest_reading_state(session: AsyncSession, node_id: str, contaminant_id: str) -> Optional[str]:
    row = (
        await session.execute(
            select(ContaminantReadingRow.final_state)
            .join(SummaryWindow, ContaminantReadingRow.summary_id == SummaryWindow.id)
            .where(SummaryWindow.node_id == node_id, ContaminantReadingRow.contaminant_id == contaminant_id)
            .order_by(SummaryWindow.t_end.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


def current_tau(edge: Edge, parent_latest_speed_mps: Optional[float]) -> float:
    """Edge.tau_base_s is the registration-time seed estimate (app/topology.py); refine it using
    the parent's most recent observed speed when available, per README."""
    if parent_latest_speed_mps and parent_latest_speed_mps > 0.05:
        return edge.length_m / parent_latest_speed_mps
    return edge.tau_base_s


def _confidence(u: float, sigma_u: float) -> float:
    z = abs(u) / max(sigma_u, 1e-9)
    return max(0.0, min((z - 2.0) / 3.0, 0.99))


async def classify_cause(
    session: AsyncSession,
    u: float,
    m_observed: float,
    m_expected: float,
    node_id: str,
    parent_contribs: list[tuple[str, Edge, float, float]],
    contaminant_id: str,
) -> dict:
    """Taxonomy per spec §4.4: pass-through, local injection, cumulative confluence, dilution
    loss. `parent_contribs` is [(parent_id, Edge, tau, mass_contribution), ...] — empty for a
    headwater, in which case `m_expected` is that node's own preceding-window baseline mass
    (see run_tracing), not a trivial 0: comparing "explained" against a flat absolute fraction
    of M_observed conflates a small, legitimate propagation-model mismatch (dispersion vs the
    simplified shift-and-decay estimate) with a genuine new local source. Comparing the *share*
    of M_observed that upstream/baseline mass accounts for is the more robust signal, since
    real dispersive spreading routinely leaves 10-20% unexplained by a linear model without that
    being a new source."""
    fraction_expected = m_expected / max(m_observed, 1e-9)

    if not parent_contribs:
        if fraction_expected >= EXPLAINED_FRACTION:
            return {"type": "pass_through", "detail": "in line with this headwater node's own recent baseline"}
        return {
            "type": "local_injection",
            "detail": "headwater node -- mass jumped well above its own recent baseline, no upstream to attribute it to",
        }

    if fraction_expected >= EXPLAINED_FRACTION:
        node_state = await _latest_reading_state(session, node_id, contaminant_id)
        dominant_parent_id = max(parent_contribs, key=lambda pc: pc[3])[0]
        if node_state in ("WARN", "RED"):
            summary = await _latest_summary(session, node_id)
            for parent_id, _edge, _tau, _mass in parent_contribs:
                parent_summary = await _latest_summary(session, parent_id)
                if summary and parent_summary and parent_summary.q_m3s > 0 and summary.q_m3s < DILUTION_Q_DROP_FRACTION * parent_summary.q_m3s:
                    return {
                        "type": "dilution_loss",
                        "parent_id": parent_id,
                        "detail": "mass balance is explained, but discharge dropped sharply relative to this parent -- concentration rose from reduced dilution, not new mass",
                    }
            return {"type": "pass_through", "dominant_parent_id": dominant_parent_id, "detail": "mass is explained by upstream parents; elevated state carried through from upstream"}
        return {"type": "pass_through", "dominant_parent_id": dominant_parent_id, "detail": "mass balance closes; no local anomaly"}

    if fraction_expected <= NEGLIGIBLE_FRACTION:
        return {"type": "local_injection", "detail": "observed mass far exceeds anything upstream parents could explain"}

    if len(parent_contribs) >= 2:
        parent_states = [await _latest_reading_state(session, p, contaminant_id) for p, _e, _t, _m in parent_contribs]
        node_state = await _latest_reading_state(session, node_id, contaminant_id)
        if all(s in (None, "SAFE") for s in parent_states) and node_state in ("WARN", "RED"):
            return {
                "type": "cumulative_confluence",
                "parent_ids": [p for p, _e, _t, _m in parent_contribs],
                "detail": "each parent individually sub-limit, but their combined mass pushes this confluence over threshold",
            }

    # Ambiguous middle ground (NEGLIGIBLE_FRACTION < fraction_expected < EXPLAINED_FRACTION):
    # a genuinely new, coincidental local source exactly here is far less likely than
    # accumulated shift-and-decay model error against the simulator's actual dispersive
    # transport, which measurably degrades with each additional hop/reach length even for a
    # pure pass-through -- default to pass-through with the partial explanation noted, rather
    # than manufacturing a new "source" at every node downstream of a real one.
    dominant_parent_id = max(parent_contribs, key=lambda pc: pc[3])[0]
    return {
        "type": "pass_through",
        "dominant_parent_id": dominant_parent_id,
        "detail": "only partially explained by upstream parents (dispersion/model mismatch), but no evidence of a distinct new local source",
    }


async def record_incident_candidate(
    session: AsyncSession, contaminant_id: str, node_id: str, cause: dict, confidence: float, t_start: datetime, t_end: datetime
) -> Incident:
    """Merges into any still-open (NEW/ONGOING/ESCALATING) incident for this contaminant rather
    than filtering by recency: Incident.created_at/updated_at are real wall-clock time
    (server_default=func.now()) while every other timestamp this engine reasons about is
    simulated time, which races arbitrarily far ahead of real time at SIM_TIME_SCALE>1 --
    comparing the two directly would only "match" by coincidence. One open incident per
    contaminant at a time is an accepted simplification for the hackathon's single-episode
    demos; resolving/closing incidents (so an unrelated later episode doesn't merge into an old
    one) is a natural follow-up, not implemented here."""
    existing = (
        await session.execute(
            select(Incident)
            .where(Incident.contaminant_id == contaminant_id, Incident.status.in_([IncidentStatus.NEW, IncidentStatus.ONGOING, IncidentStatus.ESCALATING]))
            .order_by(Incident.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    cause_entry = {**cause, "node_id": node_id, "confidence": confidence}

    if existing is None:
        incident = Incident(
            status=IncidentStatus.NEW, contaminant_id=contaminant_id, candidate_causes=[cause_entry],
            window_t_start=t_start, window_t_end=t_end,
        )
        session.add(incident)
        await session.flush()
    else:
        incident = existing
        causes = [c for c in (incident.candidate_causes or []) if c.get("node_id") != node_id]
        causes.append(cause_entry)
        causes.sort(key=lambda c: c.get("confidence", 0.0), reverse=True)
        incident.candidate_causes = causes
        incident.window_t_start = min(incident.window_t_start, t_start) if incident.window_t_start else t_start
        incident.window_t_end = max(incident.window_t_end, t_end) if incident.window_t_end else t_end
        if incident.status == IncidentStatus.NEW:
            incident.status = IncidentStatus.ONGOING

    link_exists = (
        await session.execute(select(IncidentNode).where(IncidentNode.incident_id == incident.id, IncidentNode.node_id == node_id))
    ).scalar_one_or_none()
    if link_exists is None:
        session.add(IncidentNode(incident_id=incident.id, node_id=node_id))

    await session.flush()
    return incident


async def run_periodic_scan() -> None:
    """APScheduler job (every 60-120s, see app/main.py): sweeps every mass-bearing contaminant
    over its trailing tracing window. Alert-triggered traces (app/mqtt_ingestion.py) don't wait
    for this; this catches anomalies that never crossed an individual node's WARN/RED threshold
    but still don't mass-balance (e.g. a slow confluence build-up)."""
    registry = get_registry()
    for contaminant_id in registry.live_stream_ids():
        if contaminant_id in NON_MASS_CONTAMINANTS:
            continue
        try:
            await run_tracing(contaminant_id)
        except Exception:
            logger.exception("periodic trace scan failed for %s", contaminant_id)


async def _latest_known_time(session: AsyncSession, contaminant_id: str) -> Optional[datetime]:
    """central-system has no clock of its own (§7) — every timestamp it reasons about comes
    from node/'s SimClock, which at any SIM_TIME_SCALE > 1 races ahead of (or, before the first
    summary arrives, simply doesn't correspond to) real wall-clock time. "Now" for tracing
    purposes has to mean "as of the most recent data we've actually received", not
    datetime.now(timezone.utc) — using real time here would silently never match any row once
    the sim clock has drifted, which is exactly the failure mode this guards against."""
    return (
        await session.execute(
            select(func.max(SummaryWindow.t_end))
            .join(ContaminantReadingRow, ContaminantReadingRow.summary_id == SummaryWindow.id)
            .where(ContaminantReadingRow.contaminant_id == contaminant_id)
        )
    ).scalar()


async def run_tracing(contaminant_id: str, window_seconds: Optional[float] = None, t_end: Optional[datetime] = None) -> list[Incident]:
    registry = get_registry()
    contam = registry.get(contaminant_id)
    if contam is None or contaminant_id in NON_MASS_CONTAMINANTS:
        return []

    incidents: list[Incident] = []

    async with SessionLocal() as session:
        if t_end is None:
            t_end = await _latest_known_time(session, contaminant_id)
        if t_end is None:
            return []  # no data for this contaminant yet

        window_s = window_seconds or await compute_tracing_window_s(session)
        t_start = t_end - timedelta(seconds=window_s)

        node_ids = await _nodes_carrying(session, contaminant_id)
        if not node_ids:
            return []
        edges = await _edges_among(session, node_ids)
        order = _topological_sort(node_ids, edges)

        parents_of: dict[str, list[Edge]] = defaultdict(list)
        for e in edges:
            parents_of[e.child_node_id].append(e)

        k = contam.decay_rate_per_s
        speed_cache: dict[str, Optional[float]] = {}

        for node_id in order:
            if not await _has_coverage(session, node_id, contaminant_id, t_start, t_end):
                continue  # nothing observed for this node in the current window yet

            m_observed = await get_mass(session, node_id, contaminant_id, t_start, t_end)
            var_observed = await _mass_variance(session, node_id, contaminant_id, t_start, t_end)

            edges_in = parents_of.get(node_id, [])
            parent_contribs: list[tuple[str, Edge, float, float]] = []
            m_expected = 0.0
            var_expected = 0.0
            fully_covered = True

            if edges_in:
                for e in edges_in:
                    if e.parent_node_id not in speed_cache:
                        parent_summary = await _latest_summary(session, e.parent_node_id)
                        speed_cache[e.parent_node_id] = parent_summary.speed_mps if parent_summary else None
                    tau = current_tau(e, speed_cache[e.parent_node_id])
                    shifted_start = t_start - timedelta(seconds=tau)
                    shifted_end = t_end - timedelta(seconds=tau)
                    if not await _has_coverage(session, e.parent_node_id, contaminant_id, shifted_start, shifted_end):
                        # the parent didn't exist yet (or hadn't reported) at the tau-shifted
                        # time -- treating that as "parent contributed 0" would make this node
                        # look like unexplained local injection purely from a cold-start gap,
                        # not a real anomaly. Bail on evaluating this node this round entirely.
                        fully_covered = False
                        break
                    decay = math.exp(-k * tau)
                    parent_mass = await get_mass(session, e.parent_node_id, contaminant_id, shifted_start, shifted_end)
                    contribution = parent_mass * decay
                    parent_contribs.append((e.parent_node_id, e, tau, contribution))
                    m_expected += contribution
                    var_expected += (decay**2) * await _mass_variance(session, e.parent_node_id, contaminant_id, shifted_start, shifted_end)
            else:
                # Headwater: no parents to validate against, so M_expected=0 would make every
                # normal reading look like an "anomaly" (M_observed - 0 always exceeds 2*sigma
                # for any non-trivial baseline mass). Fall back to comparing against this node's
                # own mass during the immediately preceding window of equal duration -- a
                # legitimate secondary detection mode (rolling-baseline anomaly detection)
                # distinct from junction mass balance, used only where mass balance is undefined.
                baseline_start = t_start - timedelta(seconds=window_s)
                if not await _has_coverage(session, node_id, contaminant_id, baseline_start, t_start):
                    fully_covered = False  # not enough history yet to establish a baseline
                else:
                    m_expected = await get_mass(session, node_id, contaminant_id, baseline_start, t_start)
                    var_expected = await _mass_variance(session, node_id, contaminant_id, baseline_start, t_start)

            if not fully_covered:
                continue

            u = m_observed - m_expected
            sigma_u = max((var_observed + var_expected) ** 0.5, UNCERTAINTY_FLOOR_G)

            if abs(u) > 2 * sigma_u:
                cause = await classify_cause(session, u, m_observed, m_expected, node_id, parent_contribs, contaminant_id)
                confidence = _confidence(u, sigma_u)
                incident = await record_incident_candidate(session, contaminant_id, node_id, cause, confidence, t_start, t_end)
                incidents.append(incident)
            else:
                for e in edges_in:
                    if not e.validated:
                        e.validated = True

        await session.commit()

    return incidents
