import { describe, expect, it } from "vitest";

import type { Column, FieldCondition, Form } from "../../lib/types";
import { buildRecordData, type ResolvedField } from "../records/recordValues";
import { OPERATORS } from "../views/operators";
import {
  applyFieldConditions,
  conditionMet,
  evaluateCondition,
} from "./conditions";

function column(over: Partial<Column> = {}): Column {
  return {
    uuid: "status",
    name: "status",
    display_name: "Status",
    type: "text",
    config: {},
    ...over,
  } as Column;
}

const STATUS = column();
const COLUMNS = [STATUS];

function met(
  operator: string,
  value: unknown,
  cell: unknown,
  columns = COLUMNS,
) {
  return evaluateCondition(
    { column_uuid: "status", operator, value },
    { status: cell },
    columns,
  );
}

/**
 * The drift guard.
 *
 * There are two implementations of one operator vocabulary — this one, and
 * `filtering.py::_condition` in SQL. An operator added to the filter editor and not
 * here would return null, which reads as "no opinion" and silently shows a field the
 * condition was written to hide. This fails the moment `OPERATORS` grows.
 */
describe("every operator the filter editor offers", () => {
  it.each(OPERATORS.map((o) => o.value))("%s is handled", (operator) => {
    expect(met(operator, "Rejected", "Rejected")).not.toBeNull();
  });
});

describe("evaluateCondition", () => {
  it("compares text with is and is not", () => {
    expect(met("is", "Rejected", "Rejected")).toBe(true);
    expect(met("is", "Rejected", "Approved")).toBe(false);
    expect(met("is_not", "Rejected", "Approved")).toBe(true);
    expect(met("is_not", "Rejected", "Rejected")).toBe(false);
  });

  /**
   * `NULL = 'x'` is NULL in SQL, and a NULL clause keeps the row out — so an empty
   * cell satisfies neither "is" nor "is not". Worth pinning because "is not" reads as
   * though an empty cell ought to qualify.
   */
  it("answers false for both is and is not on an empty cell", () => {
    expect(met("is", "Rejected", null)).toBe(false);
    expect(met("is_not", "Rejected", null)).toBe(false);
    expect(met("is_empty", null, null)).toBe(true);
  });

  it("matches contains without regard to case, as ilike does", () => {
    expect(met("contains", "reject", "Rejected late")).toBe(true);
    expect(met("does_not_contain", "reject", "Approved")).toBe(true);
    expect(met("does_not_contain", "reject", "Rejected")).toBe(false);
  });

  it("treats an empty string and an empty array as empty", () => {
    expect(met("is_empty", null, "")).toBe(true);
    expect(met("is_empty", null, [])).toBe(true);
    expect(met("is_not_empty", null, "x")).toBe(true);
    expect(met("is_not_empty", null, "")).toBe(false);
  });

  /** The `"9" > "10"` trap `_numeric` exists to avoid. */
  it("compares a number column numerically, not as text", () => {
    const cols = [column({ uuid: "status", type: "number" })];
    expect(met(">", 9, 10, cols)).toBe(true);
    expect(met("<", 9, 10, cols)).toBe(false);
    expect(met(">=", 10, 10, cols)).toBe(true);
  });

  it("compares a text column as text", () => {
    expect(met(">", "apple", "banana")).toBe(true);
    expect(met("<", "banana", "apple")).toBe(true);
  });

  /**
   * A calculated column has no fixed output type, so the value being compared against
   * is the only signal for which comparison was meant — `_numeric_comparable`'s rule.
   */
  it("compares a calculated column numerically only against a number", () => {
    const cols = [column({ uuid: "status", type: "calculated" })];
    expect(met(">", 9, 10, cols)).toBe(true);
    expect(met(">", "9", "10", cols)).toBe(false); // text: "10" < "9"
  });

  it("serializes a Date the way the server stores it before comparing", () => {
    const cols = [column({ uuid: "status", type: "date" })];
    expect(met("is", "2026-07-12", new Date(2026, 6, 12), cols)).toBe(true);
    expect(met("is", "2026-07-13", new Date(2026, 6, 12), cols)).toBe(false);
  });

  /**
   * `_field`'s rule: records outlive the `multiple` setting that shaped them, so a
   * single-valued column whose cell still holds an array reads its first element.
   */
  it("takes an array cell's first element unless the column is multiple", () => {
    expect(met("is", "a", ["a", "b"])).toBe(true);
    const multi = [column({ uuid: "status", config: { multiple: true } })];
    expect(met("is", "a", ["a", "b"], multi)).toBe(false);
    expect(met("contains", "b", ["a", "b"], multi)).toBe(true);
  });

  it("has no opinion about a column that is not there", () => {
    expect(
      evaluateCondition(
        { column_uuid: "gone", operator: "is", value: "x" },
        {},
        COLUMNS,
      ),
    ).toBeNull();
  });

  it("has no opinion about an operator it does not know", () => {
    expect(met("matches_regex", "x", "x")).toBeNull();
  });
});

describe("conditionMet", () => {
  function condition(
    over: Partial<FieldCondition["when"]> = {},
  ): FieldCondition {
    return {
      when: { conjunction: "and", conditions: [], ...over },
      effect: "hide",
    };
  }

  it("is true when there is no condition at all", () => {
    expect(conditionMet(null, {}, COLUMNS)).toBe(true);
    expect(conditionMet(undefined, {}, COLUMNS)).toBe(true);
  });

  /** Empty means always, matching `apply_filters` on an empty filter group — so a
   *  half-built block is inert rather than hiding what it was meant to reveal. */
  it("is true when the condition list is empty", () => {
    expect(conditionMet(condition(), {}, COLUMNS)).toBe(true);
  });

  it("requires all conditions under and, any under or", () => {
    const both = [
      { column_uuid: "status", operator: "is", value: "Rejected" },
      { column_uuid: "status", operator: "contains", value: "zzz" },
    ];
    const values = { status: "Rejected" };
    expect(conditionMet(condition({ conditions: both }), values, COLUMNS)).toBe(
      false,
    );
    expect(
      conditionMet(
        condition({ conjunction: "or", conditions: both }),
        values,
        COLUMNS,
      ),
    ).toBe(true);
  });

  /**
   * Fail *open*. A condition naming a deleted column cannot be answered, and hiding a
   * field on the strength of an unanswerable question takes it off the screen with
   * nothing to explain why — the reason `_condition` drops such a clause rather than
   * failing the query.
   */
  it("shows the field when every condition is unanswerable", () => {
    expect(
      conditionMet(
        condition({
          conditions: [{ column_uuid: "gone", operator: "is", value: "x" }],
        }),
        {},
        COLUMNS,
      ),
    ).toBe(true);
  });

  it("still answers on the conditions it can read", () => {
    expect(
      conditionMet(
        condition({
          conditions: [
            { column_uuid: "gone", operator: "is", value: "x" },
            { column_uuid: "status", operator: "is", value: "Approved" },
          ],
        }),
        { status: "Rejected" },
        COLUMNS,
      ),
    ).toBe(false);
  });
});

describe("applyFieldConditions", () => {
  const REASON = column({
    uuid: "reason",
    name: "reason",
    display_name: "Reason",
  });
  const columns = [STATUS, REASON];
  const fields: ResolvedField[] = [
    { column: STATUS, enabled: true, label: "Status" },
    { column: REASON, enabled: true, label: "Reason" },
  ];

  function formWith(effect: "hide" | "disable"): Form {
    return {
      uuid: "f1",
      name: "create",
      display_name: "Create",
      type: "create",
      is_default: true,
      permissions_inherit: true,
      config: {
        fields: [
          { column_uuid: "status", included: true, enabled: true, order: 0 },
          {
            column_uuid: "reason",
            included: true,
            enabled: true,
            order: 1,
            condition: {
              when: {
                conjunction: "and",
                conditions: [
                  { column_uuid: "status", operator: "is", value: "Rejected" },
                ],
              },
              effect,
            },
          },
        ],
      },
    } as Form;
  }

  it("drops a hidden field and keeps a met one", () => {
    const hidden = applyFieldConditions(
      fields,
      formWith("hide"),
      { status: "Approved" },
      columns,
    );
    expect(hidden.map((f) => f.column.uuid)).toEqual(["status"]);

    const shown = applyFieldConditions(
      fields,
      formWith("hide"),
      { status: "Rejected" },
      columns,
    );
    expect(shown.map((f) => f.column.uuid)).toEqual(["status", "reason"]);
  });

  it("keeps a disabled field but clears enabled", () => {
    const applied = applyFieldConditions(
      fields,
      formWith("disable"),
      { status: "Approved" },
      columns,
    );
    expect(applied.map((f) => [f.column.uuid, f.enabled])).toEqual([
      ["status", true],
      ["reason", false],
    ]);
  });

  /**
   * The reason neither effect needed a change to `buildRecordData`: it already skips
   * a field that is not enabled, and a dropped field was never in the list. So an
   * unmet field's value is not sent, and since `PATCH /items/{uuid}` merges rather
   * than replaces, whatever the record already held there survives.
   */
  it.each(["hide", "disable"] as const)(
    "does not send an unmet field's value when the effect is %s",
    (effect) => {
      const applied = applyFieldConditions(
        fields,
        formWith(effect),
        { status: "Approved", reason: "typed earlier" },
        columns,
      );
      const data = buildRecordData(
        applied,
        { status: "Approved", reason: "typed earlier" },
        true,
      );
      expect(data).toEqual({ status: "Approved" });
    },
  );

  it("leaves every field alone when the form has no conditions", () => {
    expect(applyFieldConditions(fields, null, {}, columns)).toEqual(fields);
  });
});

/**
 * The browser half of `resolve_filter_token`.
 *
 * Same substitution, same place in the evaluation, against this browser's clock
 * rather than the account's recorded zone — which is a cache of it, so the two agree
 * about what day it is. Dates are computed here rather than written out, because a
 * literal would be wrong tomorrow, which is the property the tokens exist for.
 */
describe("today and now in a condition", () => {
  const DUE = column({ uuid: "due", name: "due", type: "date" });
  const AT = column({ uuid: "at", name: "at", type: "datetime" });

  function on(col: Column, operator: string, value: unknown, cell: unknown) {
    return evaluateCondition(
      { column_uuid: col.uuid, operator, value },
      { [col.uuid]: cell },
      [col],
    );
  }

  const day = 24 * 60 * 60 * 1000;
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
      d.getDate(),
    ).padStart(2, "0")}`;
  const today = new Date();

  it("compares a date column against the day it is asked on", () => {
    expect(on(DUE, ">=", "today", iso(today))).toBe(true);
    expect(on(DUE, ">=", "today", iso(new Date(+today + day)))).toBe(true);
    expect(on(DUE, ">=", "today", iso(new Date(+today - day)))).toBe(false);
    expect(on(DUE, "is", "today", iso(today))).toBe(true);
  });

  /** "today" is midnight on a datetime, so the morning is already behind it. */
  it("separates midnight from the current reading on a datetime column", () => {
    expect(on(AT, ">=", "today", `${iso(today)}T00:00:01`)).toBe(true);
    expect(on(AT, ">=", "today", `${iso(new Date(+today - day))}T23:59:59`)).toBe(
      false,
    );
    expect(on(AT, ">=", "now", `${iso(new Date(+today + day))}T09:00:00`)).toBe(
      true,
    );
  });

  /**
   * Keyed by column type, not by the value — so a text cell really holding the word
   * still compares as that word. The whole reason the substitution is not a blanket
   * string swap.
   */
  it("leaves the word alone on a column that does not accept it", () => {
    expect(met("is", "today", "today")).toBe(true);
    expect(on(DUE, "is", "now", "now")).toBe(true);
  });
});
