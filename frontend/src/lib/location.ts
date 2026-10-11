/** Location cells: `{address, lat, lng}`, and how to read one.
 *
 *  **The only cell in the system whose value is an object.** Everything else stores a
 *  scalar or a table of them, which is why this type needed changes in four backend
 *  readers (`filtering._field`, `_apply_search`, `_csv_cell`, `_render_scalar`) that
 *  no other type has ever needed. On this side the consequence is smaller but the
 *  same in kind: `String(value)` on a cell is `[object Object]`, so every surface
 *  that shows one has to come through here.
 *
 *  Mirrors `format_location` / `parse_location` in
 *  backend/src/tables/columns/validation.py — the canonical one-line text form the
 *  export writes and the import reads. Kept in step with them: a form one accepts and
 *  the other refuses is a value somebody can paste into the grid but not into a file.
 */
import type { Column } from "./types";

export interface LocationValue {
  address: string | null;
  lat: number | null;
  lng: number | null;
}

/** Anchored to the end and required to look like coordinates, which is what lets an
 *  address keep its own "@" — mirrors `_COORD_SUFFIX_RE` on the server. */
const COORD_SUFFIX_RE = /\s*@(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/;

/** A cell as a location, or null if it is not one.
 *
 *  Reads a *string* too, and that is not defensive coding: a location column can be
 *  converted from `text`, and until the apply runs its cells are still lines of text
 *  that the picker and the grid have to render as something. */
export function asLocation(value: unknown): LocationValue | null {
  if (typeof value === "string") return parseLocation(value);
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const address = typeof raw.address === "string" ? raw.address : null;
  const lat = typeof raw.lat === "number" ? raw.lat : null;
  const lng = typeof raw.lng === "number" ? raw.lng : null;
  if (!address && lat === null) return null;
  return { address, lat, lng };
}

/** Whether a location carries a pin, as opposed to only an address. */
export function hasPin(
  value: LocationValue | null,
): value is LocationValue & { lat: number; lng: number } {
  return value !== null && value.lat !== null && value.lng !== null;
}

/** The one-line text form: `12 Rizal St, Cebu City @10.31570,123.88540`.
 *
 *  Five decimals is about a metre — finer than any address is — and keeps the line
 *  readable, which matters because this is what lands in a spreadsheet cell. */
export function formatLocation(value: unknown): string {
  const location = asLocation(value);
  if (!location) return "";
  if (!hasPin(location)) return location.address ?? "";
  const coords = `@${location.lat.toFixed(5)},${location.lng.toFixed(5)}`;
  return location.address ? `${location.address} ${coords}` : coords;
}

/** Read the one-line text form back, or null if it is empty.
 *
 *  Anything that is not the coordinate suffix is the address, so a line somebody
 *  typed by hand — an address and no pin — is read as one rather than refused. */
export function parseLocation(text: string): LocationValue | null {
  const raw = text.trim();
  if (!raw) return null;
  const suffix = COORD_SUFFIX_RE.exec(raw);
  if (!suffix) return { address: raw, lat: null, lng: null };
  const address = raw.slice(0, suffix.index).trim();
  return {
    address: address || null,
    lat: Number(suffix[1]),
    lng: Number(suffix[2]),
  };
}

/** What a cell reads as in a row: the address, or the coordinates when that is all
 *  there is. Never the raw object, which renders as `[object Object]`. */
export function locationLabel(value: unknown): string {
  const location = asLocation(value);
  if (!location) return "";
  if (location.address) return location.address;
  return hasPin(location)
    ? `${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}`
    : "";
}

/** Where the picker opens when a cell is empty: the column's own centre, else a
 *  whole-world view, which is honest about knowing nothing rather than dropping
 *  somebody into an arbitrary city. */
export function defaultView(column: Column): { lat: number; lng: number; zoom: number } {
  const centre = column.config.default_center as
    | { lat?: unknown; lng?: unknown }
    | undefined;
  const zoom = column.config.default_zoom;
  const lat = typeof centre?.lat === "number" ? centre.lat : 0;
  const lng = typeof centre?.lng === "number" ? centre.lng : 0;
  return {
    lat,
    lng,
    zoom: typeof zoom === "number" ? zoom : lat || lng ? 13 : 2,
  };
}

/** The external map link for a cell, or null when there is nothing to point at.
 *
 *  A pin wins over an address because it is unambiguous — an address string is a
 *  search, and a search can land on the wrong continent. Built here rather than
 *  inline so the three map sites stay in one place. */
export function mapHref(column: Column, value: unknown): string | null {
  const location = asLocation(value);
  if (!location) return null;
  const site = column.config.map_link;

  if (hasPin(location)) {
    const { lat, lng } = location;
    if (site === "google") return `https://www.google.com/maps?q=${lat},${lng}`;
    if (site === "apple") return `https://maps.apple.com/?ll=${lat},${lng}&q=Location`;
    return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}`;
  }

  if (!location.address) return null;
  const q = encodeURIComponent(location.address);
  if (site === "google") return `https://www.google.com/maps?q=${q}`;
  if (site === "apple") return `https://maps.apple.com/?q=${q}`;
  return `https://www.openstreetmap.org/search?query=${q}`;
}
