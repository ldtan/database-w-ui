/**
 * The control every date, time and date-time in a record is typed into.
 *
 * One component rather than three, and one for both the record surfaces and a view's
 * filter box, for the reason `PhoneInput` is: these are the values whose surfaces most
 * easily drift apart, and all of them have to agree with one server function about
 * what a typed string means.
 *
 * **What it adds over the `<Calendar>` it replaces is a box you can type in.** Nominally
 * PrimeReact's calendar already had one. In practice it lost data, quietly and in two
 * places:
 *
 * - `updateValueOnInput` (calendar.esm.js:549) parses on *every keystroke* and, when
 *   the text does not parse, calls `updateModel(event, null)`. So the first character
 *   typed into a filled cell nulls the value: a half-written date destroyed the date
 *   that was already there.
 * - `onInputBlur` (calendar.esm.js:1834) then rewrites the box from that model, so text
 *   which never parsed **vanished on blur** — no message, nothing to recover it from.
 *
 * Neither is a bug to work around from outside; both follow from the input being the
 * calendar's, not ours. So the input is ours now, the calendar is behind a button, and
 * the rules live in `lib/temporal.ts` where they can be tested.
 *
 * The two rules it inherits from `PhoneInput` and must not break:
 *
 * - **It does not fight the typist.** The box holds its own draft while it is focused
 *   and only snaps to the canonical form on blur — and, unlike the phone box, it snaps
 *   only when the draft is readable. Blanking text somebody can still see is the exact
 *   failure above, and the cure must not reproduce it.
 * - **It invents nothing.** `temporalStatus` says what it thinks — in the server's own
 *   words, lifted from `_TEMPORAL` in columns/validation.py — and the server still
 *   decides.
 */
import { useRef, useState } from "react";

import { Button } from "primereact/button";
import { Calendar } from "primereact/calendar";
import { InputText } from "primereact/inputtext";
import { OverlayPanel } from "primereact/overlaypanel";
import type { OverlayPanelPassThroughOptions } from "primereact/overlaypanel";

import {
  formatTyped,
  parseTemporal,
  parseTyped,
  serializeTemporal,
  temporalBounds,
  temporalPlaceholder,
  temporalStatus,
  type TemporalStatus,
} from "../../lib/temporal";
import type { Column } from "../../lib/types";

/**
 * `value` writes a cell; `query` writes a filter condition.
 *
 * They differ in what an unreadable draft is worth. A cell keeps it — see `commit`
 * below — because discarding what somebody typed is the thing this component exists to
 * stop. A condition drops it, so `cleanConditions` throws away a half-finished filter
 * rather than saving one; that is the rule `DurationFilterValue` already follows, and a
 * dropped filter costs nothing while a dropped cell costs the edit.
 *
 * A query is also unbounded: `temporalStatus` skips the column's min/max there, because
 * a condition is a question and the server never validates one.
 */
export type TemporalInputMode = "value" | "query";

/**
 * DataTable decides whether a click belongs to the cell being edited by looking for
 * `data-pr-is-overlay` on the clicked element or any of its parents
 * (datatable.cjs.js:1232-1238). The inline `<Calendar>` marks its own panel, so days
 * and arrows are covered already; this marks the panel *around* it, so the strip of
 * chrome at its edges is too. Without it, one click on the wrong pixel closes the cell.
 */
const PICKER_PT = {
  root: { "data-pr-is-overlay": "true" },
} as OverlayPanelPassThroughOptions;

export function TemporalInput({
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
  /** A `Date` from a record draft, or the stored string a filter holds. */
  value: unknown;
  disabled?: boolean;
  onChange: (value: unknown) => void;
  mode?: TemporalInputMode;
  /**
   * Inside a grid cell, where the row may not grow. The status *line* is dropped and
   * its message moves to the input's `title`; the red ring stays, because it costs no
   * height and it is the half that says "don't press Enter yet".
   */
  compact?: boolean;
}) {
  const type = column.type;
  const panel = useRef<OverlayPanel>(null);
  const field = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  // What is being typed. Null means "nothing in hand, render from the value" — which is
  // what makes an edit landing from elsewhere (a QR fill, another user's push) show up
  // in a box nobody is typing in.
  const [draft, setDraft] = useState<string | null>(null);

  const stored = value instanceof Date ? value : parseTemporal(type, value);
  // A cell holding a string we cannot read — written before the column was temporal,
  // or handed up by this very component — shows what is in the cell, never a blank.
  const raw = typeof value === "string" ? value : "";
  const shown = draft ?? (stored ? formatTyped(type, stored) : raw);

  const status: TemporalStatus = temporalStatus(column, shown, mode);
  const bad = status.state === "invalid" || status.state === "outOfRange";
  const bounds = temporalBounds(column);

  /**
   * Send what the box now holds, in the form this mode stores.
   *
   * In value mode an unreadable draft goes up **as the string it is**. That is not a new
   * contract: `recordValues.serialize` converts a `Date` and passes anything else
   * straight through, so a readable value behaves exactly as it did before this file
   * existed, and an unreadable one reaches the server, which answers with the same
   * sentence already printed under the box. Sending `null` instead would throw away both
   * what was typed and what was there — the failure this component replaces.
   */
  function commit(text: string) {
    const parsed = parseTyped(type, text);
    if (mode === "query") {
      onChange(parsed ? serializeTemporal(type, parsed) : null);
      return;
    }
    if (parsed) onChange(parsed);
    else onChange(text.trim() === "" ? null : text);
  }

  // Committed as it goes, like `DurationField`, so a dialog saved without blurring saves
  // what is on screen.
  function enter(text: string) {
    setDraft(text);
    commit(text);
  }

  function pick(picked: Date | null) {
    setDraft(null); // the value is the source of truth again
    if (mode === "query") {
      onChange(picked ? serializeTemporal(type, picked) : null);
    } else {
      onChange(picked ?? null);
    }
    // Only a date is finished in one click. A time or a date-time takes several, and a
    // panel that shuts after the first makes a spinner feel broken.
    if (type === "date") panel.current?.hide();
    // Back to the box, as PrimeReact's own calendar does after a pick
    // (`reFocusInputField`, calendar.cjs.js:1869-1875). Not a nicety: the panel is
    // portaled to the body, so while focus is inside it every keystroke lands outside
    // the cell or dialog this field belongs to — Escape included, which is how a grid
    // cell ends up with no way to cancel but the mouse.
    box.current?.focus({ preventScroll: true });
  }

  const cls = ["temporal-field"];
  if (compact) cls.push("temporal-field-compact");
  if (mode === "query") cls.push("temporal-field-query");

  return (
    <div
      className={cls.join(" ")}
      data-type={type}
      ref={field}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          // Escape closes the picker and goes no further. Both halves matter.
          //
          // Closing it ourselves is not optional: `OverlayPanel` closes on a
          // *document-level* listener (`useGlobalOnEscapeKey`, hooks.cjs.js:389-412),
          // and inside a grid cell `inlineEdit` stops the keypress at React's root
          // container — below `document` — so that listener never runs and the panel
          // would be stuck open. This handler is deeper in the tree, so it runs first.
          //
          // Stopping here is what makes one Escape mean one thing: without it the same
          // keypress that closes the picker also cancels the cell it opened in, or
          // closes the record dialog around it.
          panel.current?.hide();
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        if (e.key === "ArrowDown" && !open && !disabled) {
          // The keyboard route into the picker, the same one the calendar's own input
          // offers (calendar.cjs.js:543-552) — the trigger is not in the tab order.
          panel.current?.show(e, field.current);
          e.preventDefault();
        }
        if (e.key === "Enter" && open) panel.current?.hide();
      }}
    >
      <InputText
        id={id}
        ref={box}
        value={shown}
        onChange={(e) => enter(e.target.value)}
        onBlur={() => {
          // Snap to the canonical rendering — but only if there is one. Text that does
          // not parse stays exactly as typed, because a box that blanks what you can
          // still see is the failure this component was written to end.
          if (shown.trim() === "" || parseTyped(type, shown)) setDraft(null);
        }}
        placeholder={temporalPlaceholder(type)}
        disabled={disabled}
        invalid={bad}
        title={compact && status.message ? status.message : undefined}
        aria-describedby={id && !compact ? `${id}-status` : undefined}
      />
      <Button
        type="button"
        icon="pi pi-calendar"
        className="temporal-trigger"
        // Out of the tab order, as PrimeReact's own trigger is (calendar.cjs.js:3939):
        // Tab stays a field-to-field motion, and ArrowDown is the way in from the box.
        tabIndex={-1}
        aria-haspopup="dialog"
        aria-label={
          type === "time"
            ? "Choose a time"
            : type === "datetime"
              ? "Choose a date and time"
              : "Choose a date"
        }
        disabled={disabled}
        // Keep the caret where it was: without this the click focuses the button,
        // blurring the box and canonicalising a draft nobody has finished.
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => panel.current?.toggle(e, field.current)}
      />
      <OverlayPanel
        ref={panel}
        className="temporal-picker"
        pt={PICKER_PT}
        onShow={() => setOpen(true)}
        onHide={() => setOpen(false)}
      >
        <Calendar
          inline
          value={parseTyped(type, shown) ?? stored ?? null}
          onChange={(e) => pick((e.value as Date | null) ?? null)}
          timeOnly={type === "time"}
          showTime={type === "datetime"}
          // Stated rather than left to the default, so the spinner reads the same
          // numbers the box does.
          hourFormat="24"
          showButtonBar={type !== "time"}
          // New here: the picker used to offer days the column would refuse. There is
          // no `minTime`, so on a `time` column a bound is the status line's job alone.
          minDate={bounds.min ?? undefined}
          maxDate={bounds.max ?? undefined}
        />
      </OverlayPanel>
      {!compact && status.message && (
        <TemporalStatusLine
          id={id ? `${id}-status` : undefined}
          status={status}
        />
      )}
    </div>
  );
}

/**
 * The one line under the box.
 *
 * Three voices, as `PhoneStatusLine` has: a failure is an error, a finished value is an
 * acknowledgement — the weekday in "Sunday, 12 July 2026" is the check nobody can do by
 * eye — and a value still being typed is a hint, which must look like neither. A
 * half-written date in a red ring is the commonest way a validating field becomes
 * something people avoid.
 */
function TemporalStatusLine({
  id,
  status,
}: {
  id?: string;
  status: TemporalStatus;
}) {
  const bad = status.state === "invalid" || status.state === "outOfRange";
  const good = status.state === "valid";
  return (
    <p
      id={id}
      className={bad ? "field-error temporal-status" : "field-hint temporal-status"}
      // `polite` rather than `assertive`: this changes on nearly every keystroke, and a
      // screen reader interrupting itself that often says less, not more.
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
