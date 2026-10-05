/**
 * The guide's contents list: every section, in the order the page renders them.
 *
 * Here rather than inside `GuidePage.tsx` so a test can read it. `guide.test.ts`
 * runs in vitest's node environment with no jsdom, so it cannot mount the page —
 * it imports this list and matches it against the `<Section>` tags in the page's
 * source text. Keeping the list as plain data is what makes that possible, and
 * mirrors `figures.ts`, which is a plain module for the same reason.
 *
 * An id here with no matching `<Section id>` is a dead link in the contents, and
 * the other way round is a section nobody can navigate to. The test fails on
 * either, so this file and the page cannot drift apart silently.
 */
export const SECTIONS: { id: string; title: string }[] = [
  { id: "what-it-is", title: "What this app is" },
  { id: "words", title: "Five words to know" },
  { id: "signing-in", title: "Signing in" },
  { id: "getting-around", title: "Getting around" },
  { id: "workspace", title: "Step 1 · Make a workspace" },
  { id: "list", title: "Step 2 · Make a list" },
  { id: "columns", title: "Step 3 · Add columns" },
  { id: "column-types", title: "The column types, explained" },
  { id: "change-type", title: "Changing a column’s type, and copying a column" },
  { id: "records", title: "Step 4 · Add records" },
  { id: "nested-lookups", title: "Linked columns that narrow each other" },
  { id: "formulas", title: "Calculated columns: writing a formula" },
  { id: "qr", title: "Add records with a QR code" },
  { id: "find", title: "Find records: search, sort, group, filter" },
  { id: "views", title: "Views: saved ways of looking" },
  { id: "forms", title: "Forms: shape the entry screen" },
  { id: "metrics", title: "Metrics: totals, averages and counts" },
  { id: "sharing", title: "Sharing and permissions" },
  { id: "transfer", title: "Moving and giving things away" },
  { id: "duplicate", title: "Duplicating a list" },
  { id: "people", title: "People, groups, and your profile" },
  { id: "import-export", title: "Import and export" },
  { id: "archive", title: "Archiving records you’re done with" },
  { id: "bin", title: "Recovering things you deleted" },
  { id: "appearance", title: "Appearance and themes" },
  { id: "tips", title: "Tips and troubleshooting" },
];
