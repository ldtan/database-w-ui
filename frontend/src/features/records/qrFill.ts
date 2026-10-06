/**
 * A QR code's JSON, turned into the record form's values.
 *
 * The payload is an object keyed by column **name** — the same key a CSV/JSON
 * import uses, so one convention covers every way a record arrives from outside
 * the app, and a QR stays writable by a person:
 *
 *     {"first_name": "Ana", "age": 30, "owner": "admin"}
 *
 * A column uuid is accepted as a key too, for a QR a machine generated.
 *
 * **This file does not validate, on purpose.** The backend validates every value
 * on write (`src/lists/columns/validation.py`), and it is the *only* thing that
 * does: a second copy of "what a link's scheme may be" or "what this choice
 * column's options are" living here would be two rulebooks to keep in step, and
 * the frontend's would be the one that silently fell behind. So a value the code
 * carries is put into the form as it is, the record is submitted, and a bad value
 * comes back as the server's own error message — which is better worded than
 * anything this file would have said anyway.
 *
 * What it *does* do is translate between JSON's types and the form's, which is a
 * shape, not a rule: `"30"` into a number for the `InputNumber`, `"yes"` into a
 * boolean for the `Checkbox`, an ISO string into the `Date` a `Calendar` holds,
 * and a person's name into the uuid a reference cell stores so its picker can
 * render it. The first two mirror the backend's own importer (`_parse_cell` in
 * `src/lists/transfer/services.py`), which is where a QR's laxness comes from.
 *
 * Every translation **falls back to the raw value** rather than dropping it: a
 * name matching no user still gets submitted, because the server resolves names
 * too and will say so precisely if it cannot. Nothing a code carries is thrown
 * away on this side of the wire.
 */
import { api } from "../../lib/api";
import { durationFormat, parseDuration } from "../../lib/duration";
import { parseLocation } from "../../lib/location";
import type { ApiUser, ApiUserGroup, Column } from "../../lib/types";
import { deserialize, isEmptyValue, type ResolvedField } from "./recordValues";

/** A key of the payload that no field could take, and why. */
export interface QrIssue {
  key: string; // as written in the QR
  message: string;
}

export interface QrFill {
  /** Form values, keyed by column uuid — in the shapes the form's inputs hold. */
  values: Record<string, unknown>;
  /** Labels of the fields that were filled, for the summary. */
  filled: string[];
  /** Keys the *form* has nowhere to put. Not values it disliked — it judges none. */
  issues: QrIssue[];
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SEARCH_LIMIT = 25;
const TRUTHY = ["true", "yes", "y", "1"];
const FALSY = ["false", "no", "n", "0"];

/**
 * The QR's text as a flat JSON object.
 *
 * Throws with a message meant for the user: a QR that turns out to be a URL or a
 * Wi-Fi credential is the common case, not a corrupt one, and "this QR code does
 * not contain JSON" is what they need to hear.
 */
export function parseQrPayload(text: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("This QR code does not contain JSON.");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(
      "This QR code's JSON must be an object of field names and values.",
    );
  }
  return parsed as Record<string, unknown>;
}

/** A number for the number input — or the raw value, for the server to reject. */
function toNumber(raw: unknown): unknown {
  if (typeof raw === "number") return raw;
  if (typeof raw === "string" && raw.trim() !== "") {
    const value = Number(raw);
    if (Number.isFinite(value)) return value;
  }
  return raw;
}

/** A boolean for the checkbox — or the raw value, for the server to reject. */
function toBoolean(raw: unknown): unknown {
  if (typeof raw === "boolean") return raw;
  const token = String(raw).trim().toLowerCase();
  if (TRUTHY.includes(token)) return true;
  if (FALSY.includes(token)) return false;
  return raw;
}

/**
 * A reference's uuid, resolved from the name the code carried.
 *
 * The *server* resolves names too, so this is not what makes a reference work —
 * it is what lets the form **show** one: a picker holds a uuid, and given the
 * bare name "admin" it would render "unknown user" over a value that is in fact
 * about to save correctly. Hence the fallback: a name we cannot resolve (or one
 * that two rows share) is passed through untouched and submitted anyway, and the
 * server has the last word on it, as it does on everything else here.
 */
async function toReference(
  listUuid: string,
  column: Column,
  raw: unknown,
): Promise<unknown> {
  if (typeof raw !== "string" || !raw.trim() || UUID_RE.test(raw.trim())) {
    return raw;
  }
  const term = raw.trim();
  try {
    const q = `q=${encodeURIComponent(term)}&limit=${SEARCH_LIMIT}`;
    let matches: string[];
    if (column.type === "lookup") {
      const rows = await api.get<
        { uuid: string; label: string | null; deleted?: boolean }[]
      >(`/lists/${listUuid}/columns/${column.uuid}/options?${q}`);
      matches = rows
        .filter((r) => !r.deleted && (r.label ?? "").toLowerCase() === term.toLowerCase())
        .map((r) => r.uuid);
    } else {
      const path = column.type === "user" ? "/auth/users/" : "/auth/groups/";
      const rows = await api.get<(ApiUser | ApiUserGroup)[]>(`${path}?${q}`);
      matches = rows
        .filter((row) => {
          // Users are matched on username, never the connected person's name —
          // the same rule PrincipalControls follows, for the same privacy reason.
          const label =
            column.type === "user"
              ? (row as ApiUser).username
              : (row as ApiUserGroup).name;
          return !row.is_deleted && label.toLowerCase() === term.toLowerCase();
        })
        .map((row) => row.uuid);
    }
    // Exactly one, or we have not identified anything: two rows sharing a name
    // mean the code did not say which, and guessing is worse than deferring.
    return matches.length === 1 ? matches[0] : raw;
  } catch {
    // The search failed (no permission on a lookup's target, say). Submit the
    // name; the server resolves it under its own permissions or explains why not.
    return raw;
  }
}

/** The value a form input can hold, from the value the QR carried. */
async function shape(
  listUuid: string,
  column: Column,
  raw: unknown,
): Promise<unknown> {
  switch (column.type) {
    case "number":
    case "rating":
      return toNumber(raw);
    case "duration":
      // A code may carry either the seconds a cell holds or a written duration
      // ("1:30"), and the same parser the grid's editor uses reads both. An
      // unreadable one falls through as text for the server to refuse, which is
      // what every other type here does with a value it cannot shape.
      return typeof raw === "string"
        ? (parseDuration(durationFormat(column), raw) ?? raw)
        : toNumber(raw);
    case "location":
      // A code carries the one-line form the export writes, so a printed label
      // and a CSV say the same thing. Parsed here rather than left as text
      // because the picker holds an object — handed a string it would show an
      // empty map over a value that is in fact about to save correctly.
      return typeof raw === "string" ? (parseLocation(raw) ?? raw) : raw;
    case "checkbox":
      return toBoolean(raw);
    case "date":
    case "time":
    case "datetime":
      // The very function the form uses to read a stored temporal value, so a QR
      // date lands in the Calendar exactly as an existing record's would — and,
      // like it, an unparseable one stays as text rather than an Invalid Date.
      return deserialize(column, raw);
    case "lookup":
    case "user":
    case "group":
      if (Array.isArray(raw)) {
        return Promise.all(raw.map((one) => toReference(listUuid, column, one)));
      }
      return toReference(listUuid, column, raw);
    default:
      return raw;
  }
}

/**
 * The QR's payload as form values, plus any key the form had nowhere to put.
 *
 * `fields` is the *create* form's resolved fields, so a QR cannot reach a column
 * the form excludes, and cannot write one the form makes read-only — the QR is
 * another way to fill this form, not a way around it. That is the only thing
 * refused here, and it is a fact about the form, not a judgement about a value.
 */
export async function fillFromQr(
  listUuid: string,
  fields: ResolvedField[],
  payload: Record<string, unknown>,
): Promise<QrFill> {
  const byKey = new Map<string, ResolvedField>();
  for (const field of fields) {
    byKey.set(field.column.name.toLowerCase(), field);
    byKey.set(field.column.uuid.toLowerCase(), field);
  }

  const values: Record<string, unknown> = {};
  const filled: string[] = [];
  const issues: QrIssue[] = [];

  for (const [key, raw] of Object.entries(payload)) {
    if (key.startsWith("_")) {
      // **Payload metadata, skipped silently.** A column name cannot begin with an
      // underscore (`NameSlug` is `^[a-z][a-z0-9_]*$`), which is what makes `_` a safe
      // reserved namespace — and what the generator uses for `_uuid`, the record's own id
      // (see `lib/qr.ts` and `transfer/services.py::QR_UUID_KEY`).
      //
      // Silently, not as an issue: reporting an unrecognised key is right for a typo in a
      // hand-written code and wrong for a key this app wrote itself, which would put a
      // spurious "there is no such field" on every scan of every generated code. Nothing
      // is lost by ignoring it here — a fill creates a *new* record, so the id of the one
      // the code came from has nothing to say about it.
      continue;
    }
    const field = byKey.get(key.trim().toLowerCase());
    if (!field) {
      issues.push({ key, message: "there is no such field on this form" });
      continue;
    }
    if (!field.enabled) {
      issues.push({ key, message: "this field is read-only" });
      continue;
    }
    // An explicit null/"" is the QR saying "leave it blank".
    if (isEmptyValue(raw)) continue;

    values[field.column.uuid] = await shape(listUuid, field.column, raw);
    filled.push(field.label);
  }

  return { values, filled, issues };
}
