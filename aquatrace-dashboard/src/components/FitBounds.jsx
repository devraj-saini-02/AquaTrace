import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";

/** Zooms the map to fit the given [lat, lon] points once they first arrive — the World
 * Simulator grows a tight local network (150-900m reaches, see node/simulator/world.py), so a
 * fixed city-scale zoom leaves every marker as an indistinguishable dot. Only fits once per
 * distinct point-count so the user's own pan/zoom during playback isn't fought on every poll. */
export default function FitBounds({ points }) {
  const map = useMap();
  const fitted = useRef(false);

  useEffect(() => {
    if (fitted.current || points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 16);
    } else {
      map.fitBounds(points, { padding: [40, 40], maxZoom: 17 });
    }
    fitted.current = true;
  }, [points, map]);

  return null;
}
