import { isColor, normalizeColor } from "../../lib/color";
import { stripHtml } from "../../lib/display";
import { durationFormat, formatDuration } from "../../lib/duration";
import { mailtoHref } from "../../lib/email";
import { linkLabel, safeHref } from "../../lib/links";
import { locationLabel, mapHref } from "../../lib/location";
import { formatPhone, telHref } from "../../lib/phone";
import { ratingScale, ratingValue } from "../../lib/rating";
import { displayTemporal, isTemporal } from "../../lib/temporal";
import type { Column, ListItem } from "../../lib/types";
import { LookupCell } from "../lookups/LookupControls";
import { PrincipalCell, type PrincipalType } from "../principals/PrincipalControls";
import { textMode } from "./recordValues";

/**
 * One record's value for one column, as the UI shows it.
 *
 * Shared by the grid and the card list — the two are different layouts of the same
 * data, and a value that renders one way in a table and another way in a card is a
 * bug waiting to happen (it already would have been: link and temporal
 * columns each have rules that live only here).
 */
/**
 * "compact" is a cell in a row of them — a grid cell, a card field — where every
 * value gets one line and the row stays the height of a row. "full" is a value
 * given the whole width of a dialog, where a multiline column may use it.
 *
 * The default is compact because that is what a table needs, and a value that
 * silently grew to twenty lines inside a grid row would be the surprising one.
 */
export type CellVariant = "compact" | "full";

export function renderCell(
  column: Column,
  row: ListItem,
  listUuid: string,
  principalUuids: Record<PrincipalType, string[]>,
  lookupUuids: Map<string, string[]>,
  variant: CellVariant = "compact",
  /** The grid is in edit mode and this cell will open an editor. Defaults to
   *  false so the card list and the read-only dialog are unaffected. */
  editing = false,
) {
  const value = row.data?.[column.uuid];
  // Reference cells are handed the uuids the *whole page* holds, and resolve them
  // themselves. They share one query key per column, so it is still one request —
  // but each cell subscribes to it, which a map passed down as a prop cannot do:
  // DataTable re-renders only when its `value` changes, so the labels would land
  // after an edit and never reach the DOM.
  if (column.type === "lookup") {
    return (
      <LookupCell
        listUuid={listUuid}
        column={column}
        value={value}
        pageUuids={lookupUuids.get(column.uuid) ?? []}
      />
    );
  }
  if (column.type === "user" || column.type === "group") {
    return (
      <PrincipalCell
        column={column}
        value={value}
        pageUuids={principalUuids[column.type]}
      />
    );
  }
  // A multi-valued cell — a `multiple` choice column today, and whatever else grows
  // one. Keyed on the value's shape rather than on `column.config.multiple`, because
  // records outlive the setting that shaped them: a cell can still hold an array on a
  // column that no longer declares one, and the reverse. That is the same rule `_field`
  // follows in SQL (`jsonb_typeof`, not the column — known-gaps.md), and asking the
  // column here would be wrong for exactly the rows that disagree with it.
  //
  // Joined with ", " to match `LookupCell` and `formula.py`'s `_MULTI_JOIN`, which
  // already documents this as the form the grid shows.
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-faint">—</span>;
    return value.join(", ");
  }
  if (value === null || value === undefined || value === "") {
    return <span className="text-faint">—</span>;
  }
  if (column.type === "checkbox") {
    return value ? "✓" : "✗";
  }
  if (column.type === "link") {
    const href = safeHref(column, value);
    // A value the scheme check rejects is shown as plain text, never an anchor.
    if (!href) return String(value);
    // Nor is it an anchor while the cell is an editor. The `stopPropagation`
    // below exists so that following a link does not also select the row, and in
    // edit mode it eats the click the cell needs — leaving a cell drawn as
    // editable that opens a browser tab instead. Edit mode is already a mode
    // where a click means something else; turning it off makes this a link again.
    if (editing) return linkLabel(column, href);
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="hover:underline"
        style={{ color: "var(--brand)" }} // themed per palette; no Tailwind token for it
        onClick={(e) => e.stopPropagation()} // don't also toggle row selection
      >
        {linkLabel(column, href)}
      </a>
    );
  }
  if (column.type === "email") {
    const href = mailtoHref(value);
    // Same two escapes as `link` above: an address the check rejects is plain
    // text, and nothing is an anchor while the cell is an editor.
    if (!href || editing) return String(value);
    return (
      <a
        href={href}
        className="hover:underline"
        style={{ color: "var(--brand)" }} // themed per palette; no Tailwind token for it
        onClick={(e) => e.stopPropagation()} // don't also toggle row selection
      >
        {String(value)}
      </a>
    );
  }
  // The one cell that is an *object*, so it must never reach the `String(value)`
  // at the bottom of this function — that renders `[object Object]`. It sits after
  // the empty check above because an empty location arrives as `null` like any
  // other empty cell; what it cannot do is fall through.
  if (column.type === "location") {
    const label = locationLabel(value);
    if (!label) return <span className="text-faint">—</span>;
    const href = mapHref(column, value);
    if (!href || editing) return label;
    return (
      <span className="inline-flex items-center gap-1">
        <span>{label}</span>
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="pi pi-external-link text-xs"
          style={{ color: "var(--brand)" }} // themed per palette; no Tailwind token for it
          title="Open in maps"
          aria-label={`Open ${label} in maps`}
          onClick={(e) => e.stopPropagation()} // don't also toggle row selection
        />
      </span>
    );
  }
  if (column.type === "phone") {
    const href = telHref(value);
    const shown = formatPhone(column, value);
    // Same two escapes as `link` and `email`: a number the parser rejects is plain
    // text, and nothing is an anchor while the cell is an editor.
    if (!href || editing) return shown || String(value);
    return (
      <a
        href={href}
        className="hover:underline whitespace-nowrap"
        style={{ color: "var(--brand)" }} // themed per palette; no Tailwind token for it
        onClick={(e) => e.stopPropagation()} // don't also toggle row selection
      >
        {shown}
      </a>
    );
  }
  if (column.type === "duration") {
    return formatDuration(durationFormat(column), value);
  }
  if (column.type === "rating") {
    // The glyphs, not a number, because that is the whole reason to pick this
    // type over `number`. Filled up to the value and hollow to the top of the
    // scale, so a 3 reads as "3 of 5" without a caption — except on a `number`
    // icon, where somebody has asked for the digits back.
    const top = ratingScale(column);
    const filled = ratingValue(column, value) ?? 0;
    if (column.config.rating_icon === "number") return `${filled}/${top}`;
    const glyph = column.config.rating_icon === "heart" ? "heart" : "star";
    return (
      <span className="whitespace-nowrap" title={`${filled} of ${top}`}>
        {Array.from({ length: top }, (_, i) => (
          <i
            key={i}
            className={`pi pi-${glyph}${i < filled ? "-fill" : ""} text-xs`}
            style={i < filled ? { color: "var(--brand)" } : undefined}
            aria-hidden
          />
        ))}
      </span>
    );
  }
  if (column.type === "color") {
    const hex = isColor(value) ? normalizeColor(value) : null;
    // A colour with no swatch is a six-character string nobody can read as a
    // colour, so an unrenderable value falls back to the text rather than
    // drawing a chip in some arbitrary fill.
    if (!hex) return String(value);
    return (
      <span className="inline-flex items-center gap-2">
        <span
          className="inline-block h-4 w-4 shrink-0 rounded border"
          style={{ background: hex, borderColor: "var(--surface-border)" }}
          aria-hidden
        />
        <span className="font-mono text-xs">{hex}</span>
      </span>
    );
  }
  // Otherwise a datetime renders as its raw stored string, seconds and all.
  if (isTemporal(column.type)) {
    return displayTemporal(column.type, value);
  }
  const mode = textMode(column);
  if (mode === "rich") {
    // Rendered as markup only where there is room for it. Safe because the
    // backend sanitises every write (columns/richtext.py) — this is why that
    // sits on the write path and not in the editor: a value could equally
    // have arrived by API or CSV import, and it lands here just the same.
    if (variant === "full") {
      return (
        <span
          className="rich-text-cell"
          dangerouslySetInnerHTML={{ __html: String(value) }}
        />
      );
    }
    // In a row, the formatting is noise and the headings would set the row
    // height. The words are what identifies the record.
    return stripHtml(String(value));
  }
  // A multiline column's line breaks are content, and collapsing them was the
  // one thing `long_text` never got right. Honoured only where there is room:
  // in the grid the value keeps its single line.
  if (mode === "multiline" && variant === "full") {
    return <span style={{ whiteSpace: "pre-wrap" }}>{String(value)}</span>;
  }
  return String(value);
}
