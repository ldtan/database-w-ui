import { describe, expect, it } from "vitest";

import type { ColumnConfig } from "../../lib/types";
import { ADVANCED_CONFIG_KEYS, hasAdvancedConfig } from "./columnAdvanced";

describe("hasAdvancedConfig", () => {
  /**
   * The rot guard. Each key is asserted one at a time, so a control moved into the
   * Advanced section without being listed here shows up as a section that stays shut
   * over a setting somebody made.
   */
  it.each(ADVANCED_CONFIG_KEYS)("opens the section for %s", (key) => {
    // A value that is "set" for every key in the list: truthy, non-empty, not false.
    expect(hasAdvancedConfig({ [key]: "x" } as ColumnConfig)).toBe(true);
  });

  it("stays shut for a column configured with nothing but the basics", () => {
    expect(hasAdvancedConfig({})).toBe(false);
    expect(hasAdvancedConfig(null)).toBe(false);
    expect(hasAdvancedConfig(undefined)).toBe(false);
  });

  /**
   * `required` and the type-specific identity controls stay on screen, so neither may
   * open a section they are not in. `options` and `target_list_uuid` are the two that
   * would be easiest to add here by mistake.
   */
  it("ignores the settings that are never folded away", () => {
    expect(hasAdvancedConfig({ required: true })).toBe(false);
    expect(hasAdvancedConfig({ options: ["a", "b"] })).toBe(false);
    expect(hasAdvancedConfig({ target_list_uuid: "l1" })).toBe(false);
    expect(hasAdvancedConfig({ display_column_uuid: "c1" })).toBe(false);
    expect(hasAdvancedConfig({ formula: "qty * price" })).toBe(false);
    expect(hasAdvancedConfig({ text_mode: "rich" })).toBe(false);
  });

  /** Off is not set: every column carries these as `false`, and reading one as a
   *  choice would open the section for all of them. */
  it("does not count a setting that is switched off", () => {
    expect(hasAdvancedConfig({ unique: false, multiple: false })).toBe(false);
    expect(hasAdvancedConfig({ label: "" })).toBe(false);
    expect(hasAdvancedConfig({ parent_column_uuid: null })).toBe(false);
  });

  /** A minimum of zero is a minimum. Falsiness alone would fold it away. */
  it("counts a numeric zero", () => {
    expect(hasAdvancedConfig({ min: 0 })).toBe(true);
    expect(hasAdvancedConfig({ max_length: 0 })).toBe(true);
  });

  it("counts a default, including a token one", () => {
    expect(hasAdvancedConfig({ default: "now" })).toBe(true);
    expect(hasAdvancedConfig({ onupdate: "current_user" })).toBe(true);
  });
});
