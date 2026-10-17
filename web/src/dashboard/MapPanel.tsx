import "leaflet/dist/leaflet.css";

import type { FeatureCollection, Point, Polygon } from "geojson";
import L from "leaflet";
import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "../api";
import { useT } from "../i18n";
import { ACCESS_PURPOSES, type AccessPurpose } from "../types";
import type { RecordItem, Region } from "./types";

type T = ReturnType<typeof useT>;
type Grid = FeatureCollection<Polygon, { observations: number; species: number }>;

interface Props {
  regions: Region[];
  fitKey: string; // refit the view when this changes (e.g. organisation switch)
  points: FeatureCollection<Point, RecordItem> | null;
  grid: Grid | null;
  mode: "points" | "grid";
  drawing: boolean;
  onDrawn: (ring: [number, number][]) => void;
  onCancelDraw: () => void;
}

function css(el: HTMLElement, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text; // never innerHTML: names may come from an LLM
  if (className) node.className = className;
  return node;
}

/** Popup content built from DOM nodes, with an audited exact-location request. */
function popup(p: RecordItem, t: T): HTMLElement {
  const root = el("div", undefined, "dash-popup");
  const img = el("img");
  img.src = p.photo_url;
  img.alt = "";
  img.loading = "lazy";
  root.append(img, el("strong", p.taxon.common_name ?? p.taxon.scientific_name));
  if (p.taxon.common_name) root.append(el("em", p.taxon.scientific_name));
  root.append(el("span", t("dashObservedOn", { date: new Date(p.observed_at).toLocaleDateString(t.locale) })));
  const tags = [
    p.rarity_tier ? t(`tier_${p.rarity_tier}`) : null,
    p.taxon.iucn_status !== "NE" ? t.dynamic(`iucn_${p.taxon.iucn_status}`, "iucn_NE") : null,
  ].filter(Boolean);
  if (tags.length) root.append(el("span", tags.join(" · ")));

  if (p.geoprivacy !== "open") {
    root.append(el("span", `🔒 ${t("approxLocation", { km: Math.round(p.accuracy_m / 1000) })}`));
    const row = el("div");
    const select = el("select");
    ACCESS_PURPOSES.filter((purpose) => purpose !== "verification").forEach((purpose) => {
      const option = el("option", t(`purpose_${purpose}`));
      option.value = purpose;
      select.append(option);
    });
    const button = el("button", t("revealExact"));
    const result = el("span", t("revealLogged"), "dash-note");
    button.onclick = async () => {
      try {
        const exact = await api.revealLocation(p.id, select.value as AccessPurpose);
        result.textContent = `${t("exactLocation")}: ${exact.latitude.toFixed(5)}, ${exact.longitude.toFixed(5)}`;
        result.className = "";
        row.remove();
      } catch (err) {
        result.textContent =
          err instanceof ApiError && err.code ? t.dynamic(`err_${err.code}`, "err_generic") : t("err_generic");
      }
    };
    row.append(select, button);
    root.append(row, result);
  }
  return root;
}

export function MapPanel({ regions, fitKey, points, grid, mode, drawing, onDrawn, onCancelDraw }: Props) {
  const t = useT();
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const groups = useRef<{ regions: L.LayerGroup; data: L.LayerGroup; draft: L.LayerGroup } | null>(null);
  const fitted = useRef<string | null>(null);
  const [vertices, setVertices] = useState<L.LatLng[]>([]);

  useEffect(() => {
    if (!container.current) return;
    const m = L.map(container.current, { worldCopyJump: true }).setView([20, 0], 2);
    // OpenStreetMap's public tiles suit development; production needs a tile provider
    // or own tile server (OSM tile usage policy).
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(m);
    groups.current = {
      regions: L.layerGroup().addTo(m),
      data: L.layerGroup().addTo(m),
      draft: L.layerGroup().addTo(m),
    };
    map.current = m;
    // Leaflet doesn't notice its container resizing (layout changes, window resize).
    const observer = new ResizeObserver(() => m.invalidateSize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      m.remove();
      map.current = null;
      fitted.current = null; // a new map (e.g. React StrictMode remount) must fit again
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const g = groups.current;
    if (!m || !g || !container.current) return;
    g.regions.clearLayers();
    const outline = css(container.current, "--ink-2");
    const layer = L.geoJSON(
      regions.map((r) => ({ type: "Feature" as const, geometry: r.geometry, properties: { name: r.name } })),
      { style: { color: outline, weight: 2, fillOpacity: 0.04, interactive: false } },
    );
    layer.addTo(g.regions);
    if (regions.length && fitted.current !== fitKey) {
      m.fitBounds(layer.getBounds(), { padding: [24, 24] });
      fitted.current = fitKey;
    }
  }, [regions, fitKey]);

  useEffect(() => {
    const g = groups.current;
    if (!g || !container.current) return;
    g.data.clearLayers();
    const surface = css(container.current, "--surface-1");
    if (mode === "points" && points) {
      for (const f of points.features) {
        const [lon, lat] = f.geometry.coordinates;
        const color = css(container.current, `--series-${f.properties.taxon.taxon_group}`);
        const marker =
          f.properties.geoprivacy === "open"
            ? L.circleMarker([lat, lon], { radius: 6, color: surface, weight: 2, fillColor: color, fillOpacity: 1 })
            : // Protected records: draw the uncertainty area, not a misleading pin.
              L.circle([lat, lon], { radius: f.properties.accuracy_m, color, weight: 1.5, fillColor: color, fillOpacity: 0.08 });
        marker.bindPopup(() => popup(f.properties, t)).addTo(g.data);
      }
    }
    if (mode === "grid" && grid) {
      const counts = grid.features.map((f) => f.properties.observations).sort((a, b) => a - b);
      const breaks = [0.2, 0.4, 0.6, 0.8].map((q) => counts[Math.floor(q * (counts.length - 1))] ?? 0);
      const steps = [1, 2, 3, 4, 5].map((i) => css(container.current!, `--seq-${i}`));
      const fill = (n: number) => steps[breaks.filter((b) => n > b).length];
      L.geoJSON(grid, {
        style: (f) => ({ fillColor: fill(f?.properties.observations ?? 0), fillOpacity: 0.75, color: surface, weight: 1 }),
        onEachFeature: (f, layer) => {
          const tip = el("span", `${f.properties.observations} · ${f.properties.species} ${t("dashSpecies").toLowerCase()}`);
          layer.bindTooltip(tip, { sticky: true });
        },
      }).addTo(g.data);
    }
  }, [points, grid, mode, t.locale]); // `t` changes identity every render; locale is what matters

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!drawing) {
      setVertices([]);
      return;
    }
    const onClick = (e: L.LeafletMouseEvent) => setVertices((v) => [...v, e.latlng]);
    m.on("click", onClick);
    m.doubleClickZoom.disable();
    return () => {
      m.off("click", onClick);
      m.doubleClickZoom.enable();
    };
  }, [drawing]);

  useEffect(() => {
    const g = groups.current;
    if (!g || !container.current) return;
    g.draft.clearLayers();
    if (!vertices.length) return;
    const accent = css(container.current, "--accent");
    L.polygon(vertices, { color: accent, weight: 2, fillOpacity: 0.1 }).addTo(g.draft);
    vertices.forEach((v) => L.circleMarker(v, { radius: 4, color: accent, fillOpacity: 1 }).addTo(g.draft));
  }, [vertices]);

  function finish() {
    if (vertices.length < 3) return;
    const ring = [...vertices, vertices[0]].map((v) => [v.wrap().lng, v.lat] as [number, number]);
    onDrawn(ring);
    setVertices([]);
  }

  return (
    <div className="stack tight">
      {drawing && (
        <div className="row center">
          <span className="dash-note grow">{t("dashDrawHint")}</span>
          <button onClick={finish} disabled={vertices.length < 3}>{t("dashFinish")}</button>
          <button onClick={onCancelDraw}>{t("cancel")}</button>
        </div>
      )}
      {/* Leaflet owns the inner element's classes; React must never re-render them. */}
      <div className={drawing ? "dash-map-frame drawing" : "dash-map-frame"}>
        <div ref={container} className="dash-map" />
      </div>
    </div>
  );
}
