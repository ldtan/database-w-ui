import { useState } from "react";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { ARCHIVE_HINT_THRESHOLD } from "../../lib/config";
import type { FilterGroup } from "../../lib/types";

const dismissKey = (listUuid: string) => `archive-hint-dismissed:${listUuid}`;

/**
 * Says when a list has grown big enough that archiving would actually help.
 *
 * The guidance is only useful if it reaches the person holding the list, and a
 * paragraph in `docs/capacity-and-performance.md` does not. What it is *not* is a
 * "your list is big" warning: archiving does nothing for the default paged
 * listing, which is a few milliseconds at any size, and everything for the reads
 * whose cost scales with the live row count — search, the count, filters and
 * sorts, and metrics that fall back to the Python scan. The wording names those.
 *
 * Three rules keep it honest, and all three are load-bearing:
 *
 * - **Only when the count is the list's own.** `total` is keyed on the *scope* —
 *   view, search, filter — so under a filter it answers "how many match", not "how
 *   big is this list". Reporting a filtered subset as the list's size would be
 *   wrong in the direction that matters (it would stay silent on a big list read
 *   through a narrow view, and could shout on a small one). So when the scope
 *   narrows anything, this says nothing rather than guessing.
 * - **Only for someone who can archive.** Gated by the caller on list-level
 *   `delete`, the same authority the Archive actions and the archive dialog take.
 * - **Dismissal sticks, per list.** A log table someone has decided to leave alone
 *   should not keep asking — but silencing it there must not silence it on a list
 *   they have not considered yet.
 */
export function ArchiveHint({
  listUuid,
  total,
  scopeIsWholeList,
  onOpenArchive,
}: {
  listUuid: string;
  total: number | undefined;
  /** False when a view filter, an ad-hoc filter or a search is narrowing `total`. */
  scopeIsWholeList: boolean;
  onOpenArchive: () => void;
}) {
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(dismissKey(listUuid)) === "1",
  );

  if (
    dismissed ||
    ARCHIVE_HINT_THRESHOLD === 0 ||
    !scopeIsWholeList ||
    total == null ||
    total < ARCHIVE_HINT_THRESHOLD
  ) {
    return null;
  }

  function dismiss() {
    localStorage.setItem(dismissKey(listUuid), "1");
    setDismissed(true);
  }

  return (
    <Message
      severity="info"
      content={
        <div className="flex items-center gap-3 flex-wrap w-full">
          <span className="flex-1 text-sm" style={{ minWidth: "16rem" }}>
            This list has {total.toLocaleString()} records. Archiving ones you are
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

/** Whether a scope is the whole list, so a count taken under it describes the
 *  list's size rather than a subset of it. Exported so the page can compute it
 *  once and the hint stays a pure render. */
export function isWholeListScope(
  q: string,
  filters: FilterGroup,
  viewHasFilters: boolean,
): boolean {
  return !q.trim() && (filters.conditions?.length ?? 0) === 0 && !viewHasFilters;
}
