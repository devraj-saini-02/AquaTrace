"""Edge geometry estimation for registration.

RegistrationManifest (protocol §2.2) intentionally carries no edge hydraulics — a real
deployment wouldn't trust a field unit to self-report reach length/roughness/slope either.
Instead we derive an initial estimate from data the manifest *does* carry: node lat/lon
(great-circle distance as reach length) and invert_level_m (bed drop -> slope). Manning's n is a
config constant per channel-type default since the manifest has no channel-material field. This
seeded estimate is a *base* value (Edge.tau_base_s); the tracing engine refines the effective
tau per run using live speed_mps from the parent's most recent summary (see app/tracing/engine.py
current_tau()).
"""
import math

from app.physics import manning_velocity, travel_time_s

EARTH_RADIUS_M = 6_371_000.0

DEFAULT_MANNING_N = 0.014  # mid-range for a concrete-lined urban drain (spec: ~0.013-0.015)
DEFAULT_SLOPE = 0.001
MIN_SLOPE = 0.0002
ASSUMED_BASELINE_LEVEL_M = 0.3  # nominal dry/moderate-flow depth used only for the initial tau seed


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def estimate_edge_geometry(
    parent_lat: float,
    parent_lon: float,
    parent_invert_m: float | None,
    child_lat: float,
    child_lon: float,
    child_invert_m: float | None,
    child_breadth_m: float,
) -> tuple[float, float, float, float]:
    """Returns (length_m, manning_n, slope, tau_base_s)."""
    length_m = max(haversine_m(parent_lat, parent_lon, child_lat, child_lon), 1.0)

    if parent_invert_m is not None and child_invert_m is not None:
        slope = max((parent_invert_m - child_invert_m) / length_m, MIN_SLOPE)
    else:
        slope = DEFAULT_SLOPE

    manning_n = DEFAULT_MANNING_N
    v0 = manning_velocity(ASSUMED_BASELINE_LEVEL_M, child_breadth_m, manning_n, slope)
    tau_base_s = travel_time_s(length_m, v0) if v0 > 0 else length_m / 0.3

    return length_m, manning_n, slope, tau_base_s
