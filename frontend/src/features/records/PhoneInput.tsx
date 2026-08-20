/**
 * The control every phone number in the app is typed into.
 *
 * One component rather than three, because a phone number is the type where the
 * surfaces most easily drift apart: the record dialog, the grid's inline cell editor
 * and a view's filter box all take the same digits and all have to agree with
 * `normalize_phone` about what those digits mean. Splitting them would mean three
 * places to keep in step with one server function.
 *
 * What it adds over the `InputText` it replaces is a **country**. A `phone` column
 * with no `default_region` refuses a national number outright — that is deliberate on
 * the server, which will not guess a country — and before this the only cure was
 * knowing to type `+63` first. The dropdown is that knowledge, made visible and
 * changeable, and `composePhone` is where the choice turns back into digits.
 *
 * Two rules it inherits and must not break:
 *
 * - **It does not fight the typist.** The box holds its own draft while it is focused
 *   and only snaps to the canonical spacing on blur, the same shape `DurationField`
 *   and the old `PhoneFilterValue` use. Reformatting a finished number under a cursor
 *   that is still moving is how a field loses characters.
 * - **It invents nothing.** `formatAsTyped` only respaces, and the one place digits
 *   are added — the country code — comes from a choice on screen. `phoneStatus` says
 *   what it thinks, and the server still decides.
 */
import { useEffect, useRef, useState } from "react";

import { Dropdown } from "primereact/dropdown";
import { InputText } from "primereact/inputtext";
import type { CountryCode } from "libphonenumber-js/max";

import {
  cleanPasted,
  composePhone,
  dialCode,
  formatAsTyped,
  initialRegion,
  normalizePhoneQuery,
  phoneCountries,
  phonePlaceholder,
  phoneStatus,
  splitPhone,
  typedRegion,
  type PhoneCountry,
  type PhoneStatus,
} from "../../lib/phone";
import type { Column } from "../../lib/types";

/**
 * `value` writes a cell; `query` writes a filter condition.
 *
 * They differ in one thing that matters and one that follows from it. A filter may
 * hold a *fragment* — `contains 917` is a real and useful condition — so a query box
 * must not compose a country code onto it, must not call it invalid, and normalises
 * only when what was typed turns out to be a whole number (`normalizePhoneQuery`).
 * The country dropdown still earns its place there: it is what decides which
 * numbering plan `0917 123 4567` is read against.
 */
export type PhoneInputMode = "value" | "query";

export function PhoneInput({
  id,
  column,
  value,
  disabled,
  onChange,
  mode = "value",
  compact = false,
}: {
  id?: string;
  column: Column;
  value: unknown;
  disabled?: boolean;
  onChange: (value: string) => void;
  mode?: PhoneInputMode;
  /**
   * Inside a grid cell, where the row may not grow. The status *line* is dropped and
   * its message moves to the input's `title`; the red ring stays, because that costs
   * no height and is the half that says "don't press Enter yet".
   */
  compact?: boolean;
}) {
  const stored = typeof value === "string" ? value : "";
  const [region, setRegion] = useState<CountryCode | undefined>(() =>
    mode === "query" ? initialRegion(column, null) : initialRegion(column, stored),
  );
  // The national part as it is being typed. Null means "nothing in hand, render from
  // the stored value" — which is what makes an edit landing from elsewhere (a QR fill,
  // another user's push) show up in a box nobody is typing in.
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (mode === "query" ? stored : splitPhone(stored).national);

  // A value replaced from outside while the box is idle re-seeds the country, so a
  // cell that gains a US number stops claiming to be a PH one.
  const lastStored = useRef(stored);
  useEffect(() => {
    if (stored === lastStored.current) return;
    lastStored.current = stored;
    if (draft !== null || mode === "query") return;
    const found = splitPhone(stored).region;
    if (found) setRegion(found);
  }, [stored, draft, mode]);

  const status: PhoneStatus =
    mode === "query"
      ? { state: "empty", message: "" }
      : phoneStatus(column, shown, region);
  const bad = status.state === "invalid" || status.state === "blocked";

  /** Commit whatever the box now holds, in the form this mode stores. */
  function commit(text: string, forRegion: CountryCode | undefined) {
    onChange(mode === "query" ? text : composePhone(forRegion, text));
  }

  function enter(next: string) {
    // A typed `+` says which country the number belongs to, and it outranks the
    // dropdown — so the dropdown follows it rather than the two disagreeing on screen.
    const from = typedRegion(next) ?? region;
    if (from !== region) setRegion(from);
    // Never in a filter box. A condition may hold a *fragment*, and respacing `9171`
    // into `917 1` would make `contains` look for a space that no stored E.164 has —
    // the filter would quietly stop matching, which is the failure this whole type
    // exists to prevent.
    const formatted = mode === "query" ? next : formatAsTyped(column, next, from);
    setDraft(formatted);
    commit(formatted, from);
  }

  // In a grid cell the dropdown competes with the number for a width neither has
  // enough of, so it appears only where it is load-bearing: on a column that names
  // no country, where without it a national number cannot be entered at all. Where
  // the column *does* name one, the picker has nothing to add that typing a `+`
  // does not already do — `enter` re-targets the country from the digits — and the
  // record dialog still offers the full control one click away.
  const showCountry = !compact || !initialRegion(column, "");

  return (
    <div className={compact ? "phone-field phone-field-compact" : "phone-field"}>
      {showCountry && (
        <Dropdown
          // Not `inputId={id}`: the label belongs to the box the number goes in.
          value={region ?? null}
          options={phoneCountries(column)}
          // Closed, it shows `PH +63`; open, the full country names. A dropdown wide
          // enough for "Bosnia and Herzegovina" would take the room the number needs,
          // and one that is not truncates to "Philippin…" — which tells you neither
          // the country nor the code. The two things a picked country still has to
          // say are exactly the code it contributes and which country that is.
          //
          // In a grid cell there is not even room for that, so it drops to the code
          // alone: a whole column of numbers is already one country's worth of
          // context, and the alternative is an editor that wraps and grows the row.
          valueTemplate={(option: PhoneCountry | null) =>
            option ? (
              <span className="phone-country-value font-mono">
                {compact ? "" : `${option.value} `}+{dialCode(option.value)}
              </span>
            ) : (
              <span className="text-faint">{compact ? "+?" : "Country"}</span>
            )
          }
          onChange={(e) => {
            const next = (e.value ?? undefined) as CountryCode | undefined;
            setRegion(next);
            // Re-commit at once. The digits in the box did not change, but what they
            // *mean* did, and a country picked and then never re-typed over has to
            // reach the value or the dropdown is decoration.
            commit(shown, next);
          }}
          filter
          showClear
          placeholder="Country"
          disabled={disabled}
          className="phone-country"
          aria-label="Country"
        />
      )}
      <InputText
        id={id}
        type="tel"
        value={shown}
        onChange={(e) => enter(e.target.value)}
        onPaste={(e) => {
          // Numbers are pasted far more often than typed, and they arrive wrapped in
          // whatever the source used: a `tel:` prefix, non-breaking spaces, en dashes.
          // Cleaning here rather than in `enter` keeps the keystroke path free of it.
          const text = cleanPasted(e.clipboardData.getData("text"));
          if (!text) return;
          e.preventDefault();
          enter(text);
        }}
        onBlur={() => {
          setDraft(null); // fall back to the canonical rendering
          if (mode !== "query") return;
          // A filter's box normalises here and only here: mid-word the text is not a
          // number yet, and rewriting it the moment the last digit lands would move
          // the cursor out from under whoever is still typing.
          const query = normalizePhoneQuery(column, shown);
          if (query !== shown) onChange(query);
        }}
        placeholder={phonePlaceholder(column, region)}
        disabled={disabled}
        invalid={bad}
        title={compact && status.message ? status.message : undefined}
        aria-describedby={id && !compact ? `${id}-status` : undefined}
      />
      {!compact && status.message && (
        <PhoneStatusLine id={id ? `${id}-status` : undefined} status={status} />
      )}
    </div>
  );
}

/**
 * The one line under the box.
 *
 * Three voices, not two: a failure is an error (`field-error`, the same class the
 * form fields use), a finished number is an acknowledgement — "Mobile · Philippines"
 * is worth saying because it is the check nobody can do by eye — and a number still
 * being typed is a hint, which must not look like either. A half-typed number in a
 * red ring is the commonest way a validating field becomes annoying.
 */
function PhoneStatusLine({ id, status }: { id?: string; status: PhoneStatus }) {
  const bad = status.state === "invalid" || status.state === "blocked";
  const good = status.state === "valid";
  return (
    <p
      id={id}
      className={bad ? "field-error phone-status" : "field-hint phone-status"}
      // `polite` rather than `assertive`: this changes on nearly every keystroke, and
      // a screen reader interrupting itself that often says less, not more.
      aria-live="polite"
    >
      {(bad || good) && (
        <i
          className={`pi ${good ? "pi-check-circle" : "pi-exclamation-circle"} text-xs`}
          style={good ? { color: "var(--brand)" } : undefined}
          aria-hidden
        />
      )}{" "}
      {status.message}
    </p>
  );
}
