import { useState } from "react";

import { Dropdown } from "primereact/dropdown";
import { InputText } from "primereact/inputtext";
import { InputNumber } from "primereact/inputnumber";
import { SelectButton } from "primereact/selectbutton";
import { Button } from "primereact/button";

import {
  durationFormat,
  durationPlaceholder,
  formatDuration,
  parseDuration,
} from "../../lib/duration";
import { ratingScale } from "../../lib/rating";
import { filterTokenLabel, filterTokens, isTemporal } from "../../lib/temporal";
import type { Column, FilterCondition } from "../../lib/types";
import { LookupFilterValue } from "../lookups/LookupControls";
import { PrincipalFilterValue } from "../principals/PrincipalControls";
import { PhoneInput } from "../records/PhoneInput";
import { TemporalInput } from "../records/TemporalInput";
import { NO_VALUE_OPS, OPERATORS } from "./operators";

interface Props {
  listUuid: string;
  columns: Column[];
  conjunction: "and" | "or";
  onConjunctionChange: (value: "and" | "or") => void;
  conditions: FilterCondition[];
  onConditionsChange: (conditions: FilterCondition[]) => void;
}

/**
 * The rows of a filter: column, operator, value — joined by one and/or.
 *
 * Shared by the view's Filter dialog and the export dialog, which filters what it
 * exports with exactly the same shape (the backend takes the same `filters` object
 * either way), so this is the one place that knows what a condition looks like.
 *
 * The value input follows the column's type, and for reference columns that is not
 * a nicety: a lookup, user or group cell stores a *uuid*, so a text box could only
 * ever be filled by pasting one. Those get a picker, and a near-miss in a text box
 * silently matching nothing is why a temporal column's box says what it makes of
 * what has been typed into it.
 */
export function FilterConditionsEditor({
  listUuid,
  columns,
  conjunction,
  onConjunctionChange,
  conditions,
  onConditionsChange,
}: Props) {
  function update(index: number, patch: Partial<FilterCondition>) {
    onConditionsChange(
      conditions.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {conditions.length > 1 && (
        <SelectButton
          value={conjunction}
          options={[
            { label: "Match all", value: "and" },
            { label: "Match any", value: "or" },
          ]}
          onChange={(e) => e.value && onConjunctionChange(e.value)}
        />
      )}

      {conditions.map((cond, i) => {
        const col = columns.find((c) => c.uuid === cond.column_uuid);
        const needsValue = !NO_VALUE_OPS.has(cond.operator);
        const options = (col?.config.options ?? []).map((o) => ({
          label: o,
          value: o,
        }));
        return (
          // Wraps rather than overflows: column + operator + value + remove is more
          // than a phone's width, so on one it becomes two lines.
          <div
            key={i}
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label={`Condition ${i + 1}`}
          >
            <Dropdown
              value={cond.column_uuid}
              options={columns.map((c) => ({
                label: c.display_name,
                value: c.uuid,
              }))}
              onChange={(e) => update(i, { column_uuid: e.value })}
              placeholder="Column"
              className="flex-1 min-w-40"
              // Each row is three unlabelled controls repeated, so without these a
              // screen reader reads "combo box, combo box, edit" three rows deep
              // with nothing to say which condition or which part of it. Numbered
              // from 1 because that is how the rows read on screen.
              aria-label={`Condition ${i + 1} column`}
            />
            <Dropdown
              value={cond.operator}
              options={OPERATORS}
              onChange={(e) => update(i, { operator: e.value })}
              placeholder="Op"
              style={{ width: 120 }}
              aria-label={`Condition ${i + 1} operator`}
            />
            {needsValue &&
              (col?.type === "number" || col?.type === "rating" ? (
                // A rating is filtered as the number it is — "at least 4 stars"
                // is the question, and the widget would only get in the way of
                // asking it.
                <InputNumber
                  value={(cond.value as number) ?? null}
                  onValueChange={(e) => update(i, { value: e.value ?? null })}
                  useGrouping={false}
                  aria-label={`Condition ${i + 1} value`}
                  min={col.type === "rating" ? 1 : undefined}
                  max={col.type === "rating" ? ratingScale(col) : undefined}
                  style={{ width: 120 }}
                />
              ) : col?.type === "duration" ? (
                // A box, not a number input: the condition is stored in seconds,
                // but nobody asking for "longer than an hour and a half" wants to
                // work out 5400. Typed the way the column is written, parsed here.
                <DurationFilterValue
                  column={col}
                  value={cond.value}
                  onChange={(v) => update(i, { value: v })}
                  ariaLabel={`Condition ${i + 1} value`}
                />
              ) : col?.type === "phone" ? (
                <PhoneFilterValue
                  column={col}
                  value={cond.value}
                  onChange={(v) => update(i, { value: v })}
                />
              ) : col?.type === "checkbox" ? (
                // A checkbox had no branch at all and fell through to the free-text
                // box, where the only two strings that match anything are "true" and
                // "false" — `data ->> key` is text and that is what JSONB spells a
                // boolean. So filtering a checkbox meant guessing the storage format,
                // and typing the obvious "Yes" returned nothing with no explanation.
                // The values here are those two strings deliberately: the backend now
                // also accepts a real JSON boolean (`_as_text`), but sending the text
                // keeps a view saved by this editor readable by an older server.
                <Dropdown
                  value={(cond.value as string) ?? null}
                  options={[
                    { label: "Checked", value: "true" },
                    { label: "Unchecked", value: "false" },
                  ]}
                  onChange={(e) => update(i, { value: e.value })}
                  placeholder="Value"
                  style={{ width: 120 }}
                  aria-label={`Condition ${i + 1} value`}
                />
              ) : col?.type === "choice" ? (
                // Typable when the column takes answers of its own: those are
                // stored as whatever was typed, so the options are no longer the
                // whole set of values a filter could be looking for.
                <Dropdown
                  value={(cond.value as string) ?? null}
                  options={options}
                  onChange={(e) => update(i, { value: e.value })}
                  editable={!!col.config.allow_other}
                  placeholder="Value"
                  style={{ width: 120 }}
                  aria-label={`Condition ${i + 1} value`}
                />
              ) : col?.type === "user" || col?.type === "group" ? (
                // Cells store a uuid, so a text box here could only ever be
                // filled by pasting one — pick the principal instead.
                <PrincipalFilterValue
                  column={col}
                  value={cond.value}
                  onChange={(v) => update(i, { value: v })}
                  ariaLabel={`Condition ${i + 1} value`}
                />
              ) : col?.type === "lookup" ? (
                // Same: a lookup cell stores a target-row uuid, not its label.
                <LookupFilterValue
                  listUuid={listUuid}
                  column={col}
                  value={cond.value}
                  onChange={(v) => update(i, { value: v })}
                  ariaLabel={`Condition ${i + 1} value`}
                />
              ) : col && isTemporal(col.type) ? (
                <TemporalFilterValue
                  column={col}
                  value={cond.value}
                  onChange={(v) => update(i, { value: v })}
                  ariaLabel={`Condition ${i + 1} value`}
                />
              ) : (
                <InputText
                  value={(cond.value as string) ?? ""}
                  onChange={(e) => update(i, { value: e.target.value })}
                  style={{ width: 120 }}
                  aria-label={`Condition ${i + 1} value`}
                />
              ))}
            <Button
              icon="pi pi-times"
              text
              severity="danger"
              aria-label="Remove condition"
              onClick={() =>
                onConditionsChange(conditions.filter((_, j) => j !== i))
              }
            />
            {/* A multi-valued cell is compared as its whole stored list, so `is`
                and `is not` ask whether the record picked *exactly that set* —
                which reads as "matches nothing" to anyone who meant "has this
                one". `contains` is the nearest thing the filter language has to
                element-wise matching, and it searches the serialized list, so it
                is a substring match: "Arts" also finds "Martial Arts". Said here
                rather than fixed, because real per-element operators are their
                own piece of work (known-gaps.md). */}
            {col?.config.multiple && !NO_VALUE_OPS.has(cond.operator) && (
              <p className="field-hint w-full">
                {cond.operator === "contains" ||
                cond.operator === "does_not_contain"
                  ? `“${col.display_name}” holds several values, so this matches anywhere in the list — including part of a longer option.`
                  : `“${col.display_name}” holds several values, so this asks for that exact set. Use “contains” to match records holding one of them.`}
              </p>
            )}
          </div>
        );
      })}

      {conditions.length === 0 && (
        <p className="field-hint">
          No conditions — every record is shown. Add one to narrow it down.
        </p>
      )}

      <div>
        <Button
          label="Add condition"
          icon="pi pi-plus"
          text
          onClick={() =>
            onConditionsChange([
              ...conditions,
              { column_uuid: "", operator: "is", value: "" },
            ])
          }
          disabled={columns.length === 0}
        />
      </div>
    </div>
  );
}

/**
 * The value side of a condition on a `phone` column.
 *
 * Cells hold E.164, so a condition typed the way somebody says a number out loud
 * ("0917 123 4567") matches nothing — the trap a plain text box would leave open,
 * silently, on the one type where the stored form and the spoken form always differ.
 *
 * So a *whole* number is normalised to E.164, and anything else is left exactly as
 * typed, which is what keeps `contains 917` working. On blur rather than per
 * keystroke: mid-word the text is not a number yet, and rewriting the box the moment
 * the last digit lands would move the cursor out from under whoever is still typing.
 *
 * All of which now lives in `PhoneInput`'s `query` mode, alongside the record editor
 * it has to agree with. What the shared control adds here is the country dropdown —
 * the thing that decides which numbering plan `0917 123 4567` is read against, and
 * without which a filter on a column with no `default_region` cannot be typed in
 * national form at all.
 */
function PhoneFilterValue({
  column,
  value,
  onChange,
}: {
  column: Column;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  return (
    <PhoneInput
      column={column}
      value={value}
      onChange={onChange}
      mode="query"
    />
  );
}

/**
 * The value side of a condition on a `duration` column.
 *
 * The condition is stored in **seconds**, because that is what the cells hold and
 * what `filtering._numeric` compares against. Nobody asking for "longer than an
 * hour and a half" wants to work 5400 out, though, so the box takes the same
 * written forms the cell editor does and converts as it goes.
 *
 * It holds its own draft while it is being typed, for the same reason the cell
 * editor does: a duration passes through strings that are not durations yet ("1:"
 * on the way to "1:30"), and rendering straight from the parsed number would empty
 * the box mid-word. An unreadable draft still writes `null` to the condition, so a
 * half-finished one is dropped by `cleanConditions` rather than saved as a stale
 * number the box has stopped showing.
 */
function DurationFilterValue({
  column,
  value,
  onChange,
  ariaLabel,
}: {
  column: Column;
  value: unknown;
  onChange: (value: unknown) => void;
  /** The row has no visible label — see FilterConditionsEditor's aria-labels. */
  ariaLabel?: string;
}) {
  const format = durationFormat(column);
  const [draft, setDraft] = useState<string | null>(null);
  const shown =
    draft ?? (typeof value === "number" ? formatDuration(format, value) : "");
  return (
    <InputText
      value={shown}
      onChange={(e) => {
        setDraft(e.target.value);
        onChange(parseDuration(format, e.target.value));
      }}
      onBlur={() => setDraft(null)} // fall back to the canonical rendering
      placeholder={durationPlaceholder(format)}
      style={{ width: 120 }}
      aria-label={ariaLabel}
    />
  );
}

// Sentinel for the temporal value selector meaning "a value I pick", spelled the
// same way the column dialog's default-value dropdown spells it.
const SPECIFIC = "__specific__";

/**
 * The value side of a condition on a `date`, `time` or `datetime` column.
 *
 * A box you can type in *and* a calendar, which is `TemporalInput` — the same control
 * a record's cells use. A bare text box was never an option here: a near-miss on the
 * stored format silently matches nothing, and a filter that quietly finds no rows is
 * indistinguishable from a filter that is right. What that box now has is the line
 * underneath saying so before Apply is pressed, which is the half a calendar alone
 * could not give. And, in front of it, the words this column understands instead of a
 * fixed value — **Today** on a date, **Now** on a time, either on a date & time.
 *
 * Storing the word rather than the date it stands for is the entire feature. A
 * condition holds a literal, so "due today or earlier" saved into a view means
 * whatever day it was saved on, forever; the word is resolved when the query runs
 * (`filtering._condition` on the server, `resolveFilterToken` for a form field's
 * condition in the browser), so the same saved view answers today's question
 * tomorrow.
 */
function TemporalFilterValue({
  column,
  value,
  onChange,
  ariaLabel,
}: {
  column: Column;
  value: unknown;
  onChange: (value: unknown) => void;
  /** The row has no visible label — see FilterConditionsEditor's aria-labels. */
  ariaLabel?: string;
}) {
  const tokens = filterTokens(column.type);
  const token =
    typeof value === "string" && tokens.includes(value) ? value : null;
  return (
    <>
      <Dropdown
        value={token ?? SPECIFIC}
        options={[
          ...tokens.map((t) => ({ label: filterTokenLabel(t), value: t })),
          { label: "Specific…", value: SPECIFIC },
        ]}
        // Clearing the value on the way to "Specific…" leaves the condition
        // incomplete, which `cleanConditions` drops — the same state a
        // freshly-added condition is in, rather than a stale date left behind.
        onChange={(e) => onChange(e.value === SPECIFIC ? null : e.value)}
        style={{ width: 118 }}
        aria-label={ariaLabel}
      />
      {token === null && (
        <TemporalInput
          column={column}
          value={value}
          onChange={onChange}
          mode="query"
        />
      )}
    </>
  );
}
