import type { Column } from "./types";

// Mirrors SAFE_LINK_SCHEMES in the backend's column validation. The server is
// the enforcement point; this is defense in depth for values that predate the
// check or arrive from an import, so a `javascript:` string can never reach an
// href attribute.
const SAFE_SCHEMES = ["http:", "https:", "mailto:", "tel:"];

export function linkSchemes(column: Column): string[] {
  const configured = column.config.allowed_schemes;
  return Array.isArray(configured) && configured.length > 0
    ? (configured as string[])
    : ["http", "https"];
}

/** The URL to put in an href, or null if it isn't one we're willing to render. */
export function safeHref(column: Column, value: unknown): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  const allowed = linkSchemes(column).map((s) => `${s}:`);
  let scheme: string;
  try {
    scheme = new URL(value.trim()).protocol.toLowerCase();
  } catch {
    return null; // not a parseable absolute URL
  }
  if (!SAFE_SCHEMES.includes(scheme) || !allowed.includes(scheme)) return null;
  return value.trim();
}

/** What the anchor reads as: the column's fixed label, else the URL itself. */
export function linkLabel(column: Column, value: string): string {
  const label = column.config.label;
  return typeof label === "string" && label.trim() ? label : value;
}
