/** Phone cells: E.164 in the database, something dialable on screen.
 *
 *  The server decides what is stored — `normalize_phone` parses with the `phonenumbers`
 *  library and writes E.164, so `+63 917 123 4567` and `0917 123 4567` become one value
 *  and `unique` means something. This module is the display half: `libphonenumber-js`
 *  describes the same numbering plans, so what is *shown* agrees with what was accepted
 *  rather than being a second, looser opinion about phone numbers.
 *
 *  Formatting never validates. A number this cannot parse is rendered as the raw string,
 *  because a cell can hold one written before the column was a `phone` column at all
 *  (a conversion from `text` normalises them, but a cell is readable meanwhile) and a
 *  value on screen must never be one that is in no cell. `phoneStatus` is the one
 *  function here that does judge a number, and it judges it only to *say* something —
 *  the server still decides what is stored.
 *
 *  The metadata is `libphonenumber-js/max` rather than the default `min`, and that is
 *  not a size oversight: `min` cannot tell a well-formed number from a dialable one, so
 *  it calls `+63 900 000 0000` valid where `phonenumbers.is_valid_number` — and
 *  `test_rejects_what_is_not_a_number` — call it invalid. A client check that says yes
 *  where the server says no is worse than no client check at all, because it turns an
 *  inline "fix this" into a save that fails. `max` also carries the line types
 *  `getType()` reads, which is the other half of what the editor shows. Measured, it
 *  costs about 22 KB gzipped on the main bundle; every import in this file comes from
 *  `/max` so that only the one metadata set is bundled.
 */
import {
  AsYouType,
  getCountries,
  getCountryCallingCode,
  getExampleNumber,
  isSupportedCountry,
  parsePhoneNumberFromString,
  validatePhoneNumberLength,
  type CountryCode,
} from "libphonenumber-js/max";
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

/** The column's allowed countries, normalised the way the server normalises them.
 *
 *  Mirrors `phone_regions` in `columns/validation.py`: upper-cased, blanks dropped.
 *  Codes this build does not know are dropped too, so a config written by a newer
 *  server cannot narrow the picker to nothing. */
export function allowedRegions(column: Column): CountryCode[] {
  const allowed = column.config.allowed_regions;
  if (!Array.isArray(allowed)) return [];
  return allowed
    .filter((r): r is string => typeof r === "string" && !!r.trim())
    .map((r) => r.trim().toUpperCase())
    // Wrapped, not passed by reference: `isSupportedCountry` reads its *second*
    // argument as metadata, and `filter` would hand it the index.
    .filter((r) => isSupportedCountry(r)) as CountryCode[];
}

export type PhoneCountry = { label: string; value: CountryCode };

/** A country's dial code, without the `+`. Wrapped so that a code this build does
 *  not know returns "" instead of throwing inside a render. */
export function dialCode(region: CountryCode | undefined): string {
  if (!region || !isSupportedCountry(region)) return "";
  return getCountryCallingCode(region);
}

const countriesCache = new Map<string, PhoneCountry[]>();

/** The countries this column's picker offers, labelled with their dial code.
 *
 *  Narrowed to `allowed_regions` when the column has them — offering a country the
 *  server will refuse is offering a mistake — and the default region hoisted to the
 *  top, because on most columns it is the answer.
 *
 *  Labelled `Philippines (+63)` rather than with a flag: Windows ships no flag emoji
 *  font, so a flag renders there as the letters "PH" in a box. The dial code is the
 *  part that matters anyway — it is what the picker actually contributes.
 *
 *  Memoised per config, the way `countryOptions` is memoised globally: this runs on
 *  every render of every open phone editor, and the list cannot change under it. */
export function phoneCountries(column: Column): PhoneCountry[] {
  return countryChoices(allowedRegions(column), phoneRegion(column));
}

/** The same list, for a column that does not exist yet.
 *
 *  Split out for the column dialog, which is choosing the very config
 *  `phoneCountries` would read. Both go through here so a country is labelled the
 *  same where a column is set up and where its cells are filled in. */
export function countryChoices(
  allowed: CountryCode[] = [],
  preferred?: CountryCode,
): PhoneCountry[] {
  const key = `${preferred ?? ""}|${allowed.join(",")}`;
  const hit = countriesCache.get(key);
  if (hit) return hit;

  const permitted = allowed.length ? new Set<string>(allowed) : null;
  const options = countryOptions()
    .filter((o) => !permitted || permitted.has(o.value))
    .map((o) => ({
      label: `${o.label} (+${getCountryCallingCode(o.value as CountryCode)})`,
      value: o.value as CountryCode,
    }));
  // The rest stay alphabetical: only the preferred one moves.
  const list =
    preferred && options.some((o) => o.value === preferred)
      ? [
          ...options.filter((o) => o.value === preferred),
          ...options.filter((o) => o.value !== preferred),
        ]
      : options;
  countriesCache.set(key, list);
  return list;
}

/** A real number from a country, written the way a format asks for.
 *
 *  The one source of every example the UI shows — a placeholder, a "keep going"
 *  hint, the format dropdown's own labels — so that they cannot describe different
 *  shapes to each other. A hand-written example is right for one country and
 *  misleading in every other, which is why none of them is hand-written. */
export function phoneExample(
  region: CountryCode | undefined,
  format: PhoneFormat,
): string {
  const example = getExampleNumber(region ?? "US", examples);
  if (!example) return "";
  if (format === "e164") return example.number;
  // A national example only where a national number would actually be accepted:
  // with no region the server takes nothing but numbers carrying a country code.
  return region && format === "national"
    ? example.formatNational()
    : example.formatInternational();
}

/** The country a phone editor should open on, or undefined to make somebody choose.
 *
 *  A value already in the cell answers for itself; otherwise the column's default,
 *  then the only country it allows. **Never the browser's locale.** A `US` guessed
 *  from `navigator.language` turns a typed `9171234567` into `+1 917 123 4567` — a
 *  real New York number — for whoever meant a Manila mobile, and they would have no
 *  way to see that the guess had been made. `normalize_phone` refuses to guess on the
 *  server for the same reason; this refuses to guess in front of it. */
export function initialRegion(column: Column, value: unknown): CountryCode | undefined {
  const stored = splitPhone(value).region;
  if (stored) return stored;
  const allowed = allowedRegions(column);
  return phoneRegion(column) ?? (allowed.length === 1 ? allowed[0] : undefined);
}

/** A stored number pulled apart into the two things the editor shows.
 *
 *  `national` is the part that goes in the box — the digits somebody would say out
 *  loud — with the country in the dropdown beside it. A value that does not parse
 *  keeps its whole self as `national`, so an editor opened on a pre-conversion cell
 *  shows what is in the cell rather than an empty box. */
export function splitPhone(value: unknown): {
  region?: CountryCode;
  national: string;
} {
  if (typeof value !== "string" || !value.trim()) return { national: "" };
  const raw = value.trim();
  const parsed = parsePhoneNumberFromString(raw);
  if (!parsed?.country) return { national: raw };
  return { region: parsed.country, national: parsed.formatNational() };
}

/** What a country choice plus a typed number add up to, for the server to read.
 *
 *  This is the one place in this module that *adds* digits to what was typed, and it
 *  is the reason the dropdown is worth having: without it, a national number on a
 *  column with no `default_region` is refused by `normalize_phone` and the only cure
 *  is knowing to type `+63` yourself. The digits it adds are a country the user
 *  picked and can see, never an inference — which is the line `initialRegion` draws.
 *
 *  Text that already carries a `+` has said which country it belongs to, and the
 *  dropdown does not get to overrule it — but it is still canonicalised, so that what
 *  this returns is always what the server is going to store when it can be known.
 *
 *  The join goes through the parser rather than through string concatenation, because
 *  a national number is not its own digits: `0917 123 4567` is `+63 917 123 4567`, and
 *  gluing the dial code on the front would make `+63 0917…`, a number that is refused
 *  and looks like the picker's fault. Only a number too incomplete to parse falls back
 *  to concatenation, and that one the server was going to refuse either way.
 *
 *  None of this touches what is *shown*: the box keeps its own draft, so a number is
 *  never rewritten under a cursor that is still moving. */
export function composePhone(region: CountryCode | undefined, typed: string): string {
  const raw = typed.trim();
  if (!raw) return raw;
  const parsed = parsePhoneNumberFromString(raw, raw.startsWith("+") ? undefined : region);
  if (parsed) return parsed.number;
  if (raw.startsWith("+") || !region) return raw;
  const digits = raw.replace(/\D/g, "");
  // Nothing to dial: hand back what was typed so the server's complaint names it.
  if (!digits) return raw;
  return `+${getCountryCallingCode(region)}${digits}`;
}

/** Text pasted from wherever people keep phone numbers, made typeable.
 *
 *  Signatures, spreadsheets and web pages carry numbers wrapped in things a parser
 *  chokes on: a `tel:` prefix, non-breaking spaces, en dashes, full-width digits. All
 *  of that is separator noise, so all of it can go.
 *
 *  It removes and rewrites separators only — the digit sequence it returns is always
 *  the digit sequence it was given, which is the same property `formatAsTyped` holds
 *  and the same one the tests assert. A paste is not a place to start inventing. */
export function cleanPasted(text: string): string {
  return (
    text
      .replace(/^\s*tel:(?:\/\/)?/i, "")
      // Full-width digits and the full-width plus, as pasted from CJK sources.
      .replace(/[\uFF10-\uFF19]/g, (d) =>
        String.fromCharCode(d.charCodeAt(0) - 0xfee0),
      )
      .replace(/\uFF0B/g, "+")
      // Every unicode space and dash a typographer reaches for, back to the ASCII
      // one. Written as escapes rather than as the characters themselves because
      // several of them (the non-breaking space, the zero-width space) are
      // invisible in an editor, and a separator nobody can see is one nobody can
      // maintain.
      .replace(/[\u00A0\u2000-\u200B\u202F\u205F\u3000]/g, " ")
      .replace(/[\u2010-\u2015\u2212\uFF0D]/g, "-")
      .replace(/\s+/g, " ")
      .trim()
  );
}

export type PhoneStatusState =
  | "empty"
  | "needsCountry"
  | "partial"
  | "invalid"
  | "blocked"
  | "valid";

export type PhoneStatus = {
  state: PhoneStatusState;
  /** One line to show under the box. Empty only while the box is. */
  message: string;
  /** Set on `valid`: what the number turned out to be. */
  region?: CountryCode;
  kind?: string;
};

/** What line types read as. Only the ones a person would recognise are named; the
 *  rest fall back to the country alone, which is still worth saying. */
const KINDS: Record<string, string> = {
  MOBILE: "Mobile",
  FIXED_LINE: "Landline",
  FIXED_LINE_OR_MOBILE: "Mobile or landline",
  TOLL_FREE: "Toll-free",
  PREMIUM_RATE: "Premium rate",
  SHARED_COST: "Shared cost",
  VOIP: "VoIP",
  PERSONAL_NUMBER: "Personal number",
  PAGER: "Pager",
  UAN: "Business",
  VOICEMAIL: "Voicemail",
};

/** Whether a not-yet-valid number is short enough to still be on its way.
 *
 *  Measured in *national* digits against a real example from the same country, so
 *  the answer is per-country rather than a guessed minimum. A number whose country
 *  is not known yet gets the benefit of the doubt: nothing can be said about digits
 *  whose numbering plan has not been identified, and silence beats an accusation. */
function stillGrowing(raw: string, region: CountryCode | undefined): boolean {
  const reader = new AsYouType(region);
  reader.input(raw);
  const country = reader.getCountry() ?? region;
  if (!country) return true;
  const example = getExampleNumber(country, examples);
  // `getNumber()` reads a half-typed number too, and its `nationalNumber` is the
  // part to measure: the national prefix somebody types (`0917…`) is not part of
  // the number, so counting raw digits would call a finished number one short.
  const typed = reader.getNumber()?.nationalNumber ?? "";
  if (!example || !typed) return true;
  return typed.length < example.nationalNumber.length;
}

/** How a half-typed or finished number stands, said the way the server would say it.
 *
 *  The point of this function is that a number the server will refuse should be
 *  refused *here*, before a save that will be rolled back — so the wording deliberately
 *  echoes `normalize_phone` in `columns/validation.py`. Where the two could disagree
 *  they are made not to: the metadata is `/max` (see the module header) so `isValid`
 *  means what `phonenumbers.is_valid_number` means, and `allowed_regions` is checked
 *  here in the same order and with the same message.
 *
 *  `partial` is not a failure. It is the state a box is in for as long as somebody is
 *  typing, and calling it invalid would put a red ring around every number on its way
 *  to being right. */
export function phoneStatus(
  column: Column,
  typed: string,
  region: CountryCode | undefined,
): PhoneStatus {
  const raw = typed.trim();
  if (!raw) return { state: "empty", message: "" };

  if (!raw.startsWith("+") && !region) {
    // The commonest refusal the server issues, said before the request goes out.
    return {
      state: "needsCountry",
      message: "Pick a country, or start with the country code — e.g. +639171234567.",
    };
  }

  const keepGoing = (): PhoneStatus => {
    const example = phonePlaceholder(column, region);
    return {
      state: "partial",
      message: example ? `Keep going — a number here looks like ${example}.` : "",
    };
  };

  const length = validatePhoneNumberLength(raw, region ? { defaultCountry: region } : {});
  if (length === "TOO_SHORT" || length === "INVALID_LENGTH") return keepGoing();
  if (length === "NOT_A_NUMBER" || length === "TOO_LONG") {
    return { state: "invalid", message: "That is not a phone number." };
  }

  const parsed = parsePhoneNumberFromString(raw, region);
  if (!parsed || !parsed.isValid()) {
    // A length check alone cannot tell "still typing" from "wrong", because most
    // countries have more than one valid length: `0917 123` is six digits, which is
    // a fine length for a Philippine landline and a third of a mobile. Reading that
    // as an error would put a red ring around a number on its way to being right.
    //
    // So the tie is broken on the number's own length against a real example from
    // its country: shorter than one, it can still become valid by typing; as long as
    // one and still not dialable, it is `+63 900 000 0000` — the right shape for no
    // operator, which is exactly what the server refuses.
    return stillGrowing(raw, parsed?.country ?? region) ? keepGoing() : {
      state: "invalid",
      message: "That is not a valid phone number.",
    };
  }

  const allowed = allowedRegions(column);
  if (allowed.length && (!parsed.country || !allowed.includes(parsed.country))) {
    return {
      state: "blocked",
      message: `This column only accepts numbers from ${allowed.join(", ")} — not ${
        parsed.country ?? "that country"
      }.`,
    };
  }

  const country = parsed.country;
  const name = country
    ? (countryOptions().find((o) => o.value === country)?.label ?? country)
    : "";
  const kind = KINDS[parsed.getType() ?? ""] ?? "";
  return {
    state: "valid",
    region: country,
    kind: kind || undefined,
    message: [kind, name].filter(Boolean).join(" · "),
  };
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
 *  what was typed, and the server has the last word on whether it is a number.
 *
 *  `region` overrides the column's default, and the editor always passes it: once
 *  there is a country dropdown on screen, spacing a number by the *column's* country
 *  while the picker says another one would space it by a plan nobody selected. */
export function formatAsTyped(
  column: Column,
  typed: string,
  region: CountryCode | undefined = phoneRegion(column),
): string {
  return new AsYouType(region).input(typed);
}

/** The country a typed number has named for itself, if it has named one.
 *
 *  Only ever answers for text carrying a `+`: that is the one case where the digits
 *  say which country they belong to, and it is the case where the dropdown has to
 *  follow the box rather than the other way round. `+1` and `+44` stay undefined
 *  until enough digits arrive to tell a country from its neighbours, which is
 *  libphonenumber being honest rather than a gap. */
export function typedRegion(typed: string): CountryCode | undefined {
  if (!typed.trim().startsWith("+")) return undefined;
  const reader = new AsYouType();
  reader.input(typed);
  return reader.getCountry();
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
export function phonePlaceholder(
  column: Column,
  /** The country the editor is actually on, which outranks the column's default —
   *  an example from a country nobody selected is an example of the wrong shape. */
  selected?: CountryCode,
): string {
  // `allowed_regions` is the next best answer when no default is set: it is still
  // this column's own country rather than a guess.
  const allowed = column.config.allowed_regions;
  const fallback = Array.isArray(allowed) ? allowed[0] : undefined;
  const region =
    selected ??
    phoneRegion(column) ??
    (typeof fallback === "string" && isSupportedCountry(fallback.toUpperCase())
      ? (fallback.toUpperCase() as CountryCode)
      : undefined);

  // A country picked on the editor makes the box a *national* box — that is what the
  // dropdown beside it means — so the example is national whatever the column's
  // display format, which governs how a saved number is drawn rather than typed.
  return phoneExample(region, selected ? "national" : phoneFormat(column));
}
