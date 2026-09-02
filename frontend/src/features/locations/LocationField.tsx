/**
 * The editor for a `location` cell: an address box, an address search, and a map
 * with a draggable pin.
 *
 * **Leaflet is driven imperatively, on purpose.** It owns its own DOM and its own
 * event loop; wrapping it in a React component tree means fighting over who may
 * touch that DOM, and the usual result is a map that re-initialises on every
 * keystroke in the address box. So the map is created once in an effect, held in a
 * ref, and updated by imperative calls — the React tree above it never re-renders
 * anything Leaflet owns.
 *
 * Loaded lazily (`import("leaflet")`), because the library and its CSS are ~150 kB
 * that only somebody editing a location column ever needs, and most lists have no
 * such column at all.
 *
 * Tiles are fetched by the browser straight from OpenStreetMap, with the
 * attribution their licence requires. The *geocoder* is not: that goes through the
 * app's own `/geocode` so the deployment has one identity and one rate limit, which
 * a browser cannot provide (see src/geocode/services.py).
 */
import { useEffect, useRef, useState } from "react";

import type { Map as LeafletMap, Marker } from "leaflet";
import { Button } from "primereact/button";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";

import { api } from "../../lib/api";
import {
  asLocation,
  defaultView,
  hasPin,
  type LocationValue,
} from "../../lib/location";
import type { Column } from "../../lib/types";

interface GeocodeResult {
  label: string;
  lat: number;
  lng: number;
}
interface GeocodeResponse {
  results: GeocodeResult[];
  available: boolean;
  detail: string | null;
}

const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export function LocationField({
  id,
  column,
  value,
  disabled,
  onChange,
}: {
  id: string;
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const location = asLocation(value);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const markerRef = useRef<Marker | null>(null);
  // The current value, readable from inside Leaflet's callbacks without making the
  // map effect depend on it — a dependency there would tear the map down and
  // rebuild it every time a pin moved.
  const valueRef = useRef<LocationValue | null>(location);
  valueRef.current = location;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  // Read-only-ness, by the same trick and for the same reason as the two above: the
  // map effect runs once per column and its Leaflet callbacks close over whatever
  // `disabled` was at that moment. A field that becomes read-only afterwards — the
  // update form marking it so, a grant changing — went on taking map clicks, and the
  // marker was created `draggable: true` unconditionally and its `dragend` never
  // checked at all. So a greyed-out address box sat above a map that still moved the
  // pin: the value visibly changed, and `buildRecordData` then dropped it on save,
  // which is an edit that disappears without a word.
  const disabledRef = useRef(disabled);
  disabledRef.current = disabled;

  function commit(next: Partial<LocationValue>) {
    const merged: LocationValue = {
      address: null,
      lat: null,
      lng: null,
      ...(valueRef.current ?? {}),
      ...next,
    };
    // Neither half filled in is an empty cell, which the server refuses as a value
    // and accepts as a null — so send the null rather than the empty shape.
    const empty = !merged.address?.trim() && merged.lat === null;
    onChangeRef.current(empty ? null : merged);
  }

  // Create the map once. Leaflet is loaded here rather than imported at the top so
  // the bundle only pays for it on a list that has a location column.
  useEffect(() => {
    let cancelled = false;
    let map: LeafletMap | null = null;

    (async () => {
      const L = await import("leaflet");
      await import("leaflet/dist/leaflet.css");
      if (cancelled || !containerRef.current || mapRef.current) return;

      const start = valueRef.current;
      const fallback = defaultView(column);
      map = L.map(containerRef.current).setView(
        hasPin(start) ? [start.lat, start.lng] : [fallback.lat, fallback.lng],
        hasPin(start) ? 16 : fallback.zoom,
      );
      L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 19 }).addTo(map);

      // Leaflet's default marker images are resolved relative to the CSS, which a
      // bundler rewrites — so they 404 and the pin is invisible. A divIcon needs no
      // image at all, which is one fewer thing the build can break.
      const icon = L.divIcon({
        className: "location-pin",
        html: '<span aria-hidden="true">📍</span>',
        iconSize: [24, 24],
        iconAnchor: [12, 22],
      });

      function place(lat: number, lng: number) {
        if (!map) return;
        if (markerRef.current) {
          markerRef.current.setLatLng([lat, lng]);
        } else {
          markerRef.current = L.marker([lat, lng], {
            icon,
            draggable: !disabledRef.current,
          }).addTo(map);
          markerRef.current.on("dragend", () => {
            // Belt and braces with `dragging.disable()` below: a drag that was
            // already under way when the field went read-only still ends here.
            if (disabledRef.current) return;
            const at = markerRef.current!.getLatLng();
            commit({ lat: at.lat, lng: at.lng });
          });
        }
      }

      if (hasPin(start)) place(start.lat, start.lng);
      map.on("click", (e: { latlng: { lat: number; lng: number } }) => {
        if (disabledRef.current) return;
        place(e.latlng.lat, e.latlng.lng);
        commit({ lat: e.latlng.lat, lng: e.latlng.lng });
      });
      mapRef.current = map;
    })();

    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
      markerRef.current = null;
    };
    // The column decides where an empty map opens, and nothing else here may
    // rebuild it — see `valueRef` above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [column.uuid]);

  // Keep the pin's own draggability in step with the field. The marker is created
  // once, whenever the first pin lands, so its `draggable` option is a snapshot;
  // this is what carries a change afterwards. Also covers the ordinary order of
  // events on a read-only field that already has a value — the marker is placed
  // during the effect above, before this runs.
  useEffect(() => {
    const dragging = markerRef.current?.dragging;
    if (!dragging) return;
    if (disabled) dragging.disable();
    else dragging.enable();
  }, [disabled, location?.lat, location?.lng]);

  // Follow a pin that changed from outside the map: a search result chosen, or the
  // dialog reopened on another record. Imperative for the reason the file's header
  // gives — React never touches Leaflet's DOM.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !hasPin(location)) return;
    const marker = markerRef.current;
    if (marker) marker.setLatLng([location.lat, location.lng]);
    map.setView([location.lat, location.lng], Math.max(map.getZoom(), 16));
  }, [location?.lat, location?.lng]);

  async function search() {
    const term = (query || location?.address || "").trim();
    if (!term) return;
    setSearching(true);
    setNotice(null);
    try {
      const body = await api.get<GeocodeResponse>(
        `/geocode/?q=${encodeURIComponent(term)}`,
      );
      setResults(body.results);
      // `available: false` is an outage or an unconfigured geocoder; an empty
      // `results` is the geocoder saying it knows nowhere by that name. Different
      // sentences, because only one of them is worth retrying.
      if (!body.available) setNotice(body.detail ?? "Address search is unavailable.");
      else if (body.results.length === 0) setNotice(`No places found for “${term}”.`);
    } catch {
      setNotice("Address search is unavailable right now.");
    } finally {
      setSearching(false);
    }
  }

  function choose(result: GeocodeResult) {
    setResults([]);
    setQuery("");
    commit({ address: result.label, lat: result.lat, lng: result.lng });
  }

  return (
    <div className="flex flex-col gap-2">
      <InputText
        id={id}
        value={location?.address ?? ""}
        onChange={(e) => commit({ address: e.target.value })}
        placeholder="Address"
        disabled={disabled}
      />

      <div className="flex items-center gap-2">
        <InputText
          className="flex-1"
          aria-label={`${column.display_name}: search for an address`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              // The field sits inside a form dialog, so Enter would otherwise
              // submit the record rather than run the search.
              e.preventDefault();
              void search();
            }
          }}
          placeholder="Search for a place…"
          disabled={disabled}
        />
        <Button
          type="button"
          icon="pi pi-search"
          label="Find"
          onClick={() => void search()}
          loading={searching}
          disabled={disabled}
        />
      </div>

      {results.length > 0 && (
        <ul className="flex flex-col gap-1 rounded border p-1" style={{ borderColor: "var(--surface-border)" }}>
          {results.map((result) => (
            <li key={`${result.lat},${result.lng},${result.label}`}>
              <button
                type="button"
                className="w-full rounded px-2 py-1 text-left text-sm hover:bg-[var(--surface-hover)]"
                onClick={() => choose(result)}
              >
                {result.label}
              </button>
            </li>
          ))}
        </ul>
      )}

      {notice && <Message severity="info" text={notice} className="w-full" />}

      <div
        ref={containerRef}
        className="rounded border"
        style={{ height: 240, borderColor: "var(--surface-border)" }}
      />

      <div className="flex items-center justify-between text-xs" style={{ color: "var(--text-muted)" }}>
        <span>
          {hasPin(location)
            ? `${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}`
            : "Click the map to drop a pin"}
        </span>
        {hasPin(location) && !disabled && (
          <Button
            type="button"
            label="Clear pin"
            text
            size="small"
            onClick={() => {
              markerRef.current?.remove();
              markerRef.current = null;
              commit({ lat: null, lng: null });
            }}
          />
        )}
      </div>
    </div>
  );
}
