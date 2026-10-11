import { useState } from "react";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { ARCHIVE_HINT_THRESHOLD } from "../../lib/config";
import type { FilterGroup } from "../../lib/types";

const dismissKey = (tableUuid: string) => `archive-hint-dismissed:${tableUuid}`;

/**
 * Says when a table has grown big enough that archiving would actually help.
 *
 * The guidance is only useful if it reaches the person holding the table, and a
 * paragraph in `docs/capacity-and-performance.md` does not. What it is *not* is a
 * "your table is big" warning: archiving does nothing for the default paged
 * listing, which is a few milliseconds at any size, and everything for the reads
 * whose cost scales with the live row count — search, the count, filters and
 * sorts, and metrics that fall back to the Python scan. The wording names those.
 *
 * Three rules keep it honest, and all three are load-bearing:
 *
 * - **Only when the count is the table's own.** `total` is keyed on the *scope* —
 *   view, search, filter — so under a filter it answers "how many match", not "how
 *   big is this table". Reporting a filtered subset as the table's size would be
 *   wrong in the direction that matters (it would stay silent on a big table read
 *   through a narrow view, and could shout on a small one). So when the scope
 *   narrows anything, this says nothing rather than guessing.
 * - **Only for someone who can archive.** Gated by the caller on table-level
 *   `delete`, the same authority the Archive actions and the archive dialog take.
 * - **Dismissal sticks, per table.** A log table someone has decided to leave alone
 *   should not keep asking — but silencing it there must not silence it on a table
 *   they have not considered yet.
 */
export function ArchiveHint({
  tableUuid,
  total,
  scopeIsWholeTable,
  onOpenArchive,
}: {
  tableUuid: string;
  total: number | undefined;
  /** False when a view filter, an ad-hoc filter or a search is narrowing `total`. */
  scopeIsWholeTable: boolean;
  onOpenArchive: () => void;
}) {
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(dismissKey(tableUuid)) === "1",
  );

  if (
    dismissed ||
    ARCHIVE_HINT_THRESHOLD === 0 ||
    !scopeIsWholeTable ||
    total == null ||
    total < ARCHIVE_HINT_THRESHOLD
  ) {
    return null;
  }

  function dismiss() {
    localStorage.setItem(dismissKey(tableUuid), "1");
    setDismissed(true);
  }

  return (
    <Message
      severity="info"
      content={
        <div className="flex items-center gap-3 flex-wrap w-full">
          <span className="flex-1 text-sm" style={{ minWidth: "16rem" }}>
            This table has {total.toLocaleString()} records. Archiving ones you are
            finished with will speed up searching, filtering and totals — archived
            records are kept, just out of the way.
          </span>
          <Button
            label="Archived records"
            icon="pi pi-box"
            text
            size="small"
            onClick={onOpenArchive}
          />
          <Button label="Dismiss" text size="small" onClick={dismiss} />
        </div>
      }
    />
  );
}

/** Whether a scope is the whole table, so a count taken under it describes the
 *  table's size rather than a subset of it. Exported so the page can compute it
 *  once and the hint stays a pure render. */
export function isWholeTableScope(
  q: string,
  filters: FilterGroup,
  viewHasFilters: boolean,
): boolean {
  return !q.trim() && (filters.conditions?.length ?? 0) === 0 && !viewHasFilters;
}
