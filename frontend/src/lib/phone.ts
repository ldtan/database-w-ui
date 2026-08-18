/** Phone cells: E.164 in the database, something dialable on screen.
 *
 *  The server decides what is stored — `normalize_phone` parses with the `phonenumbers`
 *  library and writes E.164, so `+63 917 123 4567` and `0917 123 4567` become one value
 *  and `unique` means something. This module is the display half: `libphonenumber-js`
 *  describes the same numbering plans, so what is *shown* agrees with what was accepted
 *  rather than being a second, looser opinion about phone numbers.
 *
 *  Nothing here validates. A number this cannot parse is rendered as the raw string,
 *  because a cell can hold one written before the column was a `phone` column at all
 *  (a conversion from `text` normalises them, but a cell is readable meanwhile) and a
 *  value on screen must never be one that is in no cell.
 */
import {
  AsYouType,
  getCountries,
  getExampleNumber,
  isSupportedCountry,
  parsePhoneNumberFromString,
  type CountryCode,
} from "libphonenumber-js";
import examples from "libphonenumber-js/examples.mobile.json";

import type { Column } from "./types";

export type PhoneFormat = "e164" | "national" | "international";

const FORMATS: PhoneFormat[] = ["e164", "national", "international"];

/** Every country the phone metadata knows, labelled and sorted for a dropdown.
 *
 *  The codes come from libphonenumber (so the list is exactly what the server's
 *  `SUPPORTED_REGIONS` will accept — the two ship the same underlying data), and the
 *  names from `Intl.DisplayNames`, which is in the browser already and localises
 *  itself. A build with no `Intl.DisplayNames` falls back to the bare codes rather
 *  than to an empty dropdown.
 *
 *  Computed once: 245 entries through `Intl` is cheap but not free, and the list
 *  cannot change while the page is open. */
let countryOptionsCache: { label: string; value: string }[] | null = null;

export function countryOptions(): { label: string; value: string }[] {
  if (countryOptionsCache) return countryOptionsCache;
  let names: Intl.DisplayNames | null = null;
  try {
    names = new Intl.DisplayNames(undefined, { type: "region" });
  } catch {
    names = null;
  }
  countryOptionsCache = getCountries()
    .map((code) => ({
      label: names?.of(code) ?? code,
      value: code as string,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
  return countryOptionsCache;
}

/** How this column's numbers are shown. Unset means `international`, which is the
 *  only format that reads correctly for a column holding more than one country. */
export function phoneFormat(column: Column): PhoneFormat {
  const format = column.config.display_format;
  return FORMATS.includes(format as PhoneFormat)
    ? (format as PhoneFormat)
    : "international";
}

/** The column's default region, or undefined. Checked against the library's own
 *  country list so a config this build does not know cannot throw inside a cell. */
export function phoneRegion(column: Column): CountryCode | undefined {
  const region = column.config.default_region;
  if (typeof region !== "string" || !region.trim()) return undefined;
  const code = region.trim().toUpperCase();
  return isSupportedCountry(code) ? (code as CountryCode) : undefined;
}

/** A stored number as this column writes it, or the raw string if it is not one.
 *
 *  `national` deliberately falls back to the international form for a number from
 *  outside the column's region: "0917 123 4567" printed for a US number would be a
 *  number nobody can dial, and quietly wrong is worse than inconsistently formatted. */
export function formatPhone(column: Column, value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "";
  const parsed = parsePhoneNumberFromString(value.trim());
  if (!parsed) return value;
  const format = phoneFormat(column);
  if (format === "e164") return parsed.number;
  if (format === "national" && parsed.country === phoneRegion(column)) {
    return parsed.formatNational();
  }
  return parsed.formatInternational();
}

/** The `tel:` URL for a cell, or null if it isn't a number we'll link.
 *
 *  Always the E.164 form, never the displayed one: a `tel:` a phone can act on has
 *  to carry the country code, whatever the column shows. `tel` is in the backend's
 *  `SAFE_LINK_SCHEMES`, and the scheme here is ours rather than the value's — the
 *  number is parsed before it is used, so nothing a cell holds can choose it. */
export function telHref(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = parsePhoneNumberFromString(value.trim());
  return parsed ? `tel:${parsed.number}` : null;
}

/** What a partly-typed number should read as, formatted as far as it is unambiguous.
 *
 *  Only ever *reformats* — it never adds or drops a digit — so what is submitted is
 *  what was typed, and the server has the last word on whether it is a number. */
export function formatAsTyped(column: Column, typed: string): string {
  return new AsYouType(phoneRegion(column)).input(typed);
}

/** A filter's typed value as it should be compared against stored cells.
 *
 *  Cells hold E.164, so a whole number typed the way somebody says it out loud is
 *  rewritten to the form the comparison will actually see. Anything that is not a
 *  whole number — "917", half a number, a country code on its own — is returned
 *  untouched, which is what keeps a `contains` filter working on a fragment. */
export function normalizePhoneQuery(column: Column, typed: string): string {
  const raw = typed.trim();
  if (!raw) return typed;
  const parsed = parsePhoneNumberFromString(raw, phoneRegion(column));
  return parsed?.isValid() ? parsed.number : typed;
}

/** The placeholder for this column: a real example number for the right country.
 *
 *  From libphonenumber's own example set (4 KB) rather than a literal, because a
 *  hand-written example is right for one country and misleading in every other —
 *  and the hint is the only place the *shape* of an acceptable number is stated.
 *
 *  A column with no region can only take a number carrying its own country code —
 *  that is the server's rule (`normalize_phone`) — so it gets the international
 *  form, and never a national number that would be refused if typed. */
export function phonePlaceholder(column: Column): string {
  // `allowed_regions` is the next best answer when no default is set: it is still
  // this column's own country rather than a guess.
  const allowed = column.config.allowed_regions;
  const fallback = Array.isArray(allowed) ? allowed[0] : undefined;
  const region =
    phoneRegion(column) ??
    (typeof fallback === "string" && isSupportedCountry(fallback.toUpperCase())
      ? (fallback.toUpperCase() as CountryCode)
      : undefined);

  const example = getExampleNumber(region ?? "US", examples);
  if (!example) return "";
  if (phoneFormat(column) === "e164") return example.number;
  // National only where a national number would actually be accepted.
  return region && phoneFormat(column) === "national"
    ? example.formatNational()
    : example.formatInternational();
}
