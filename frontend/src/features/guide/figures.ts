/**
 * What each picture in the guide shows — generated, do not edit.
 *
 * Written by `scripts/capture-guide.mjs` from its own `SHOTS` list, which is
 * also what took the images in `public/guide/`. Run `npm run capture:guide` to
 * rebuild both together; editing this by hand puts a caption under a picture that
 * nothing else agrees with.
 */

export interface Figure {
  /** Alternative text: what a reader who cannot see the picture is told. */
  alt: string;
  /** The line printed under it. */
  caption: string;
}

export const FIGURES: Record<string, Figure> = {
  "sign-in": {
    alt: "The sign-in screen, with username and password boxes and a Sign in button.",
    caption: "The only page you see before signing in.",
  },
  "shell": {
    alt: "The app's home page, with the navigation strip down the left-hand side.",
    caption: "Home, with the strip on the left. It stays there wherever you go.",
  },
  "nav-tree": {
    alt: "The navigation strip, with workspaces expanded to show the lists inside.",
    caption: "Click the arrow beside a workspace to see its lists; click the name to open the workspace itself.",
  },
  "workspaces": {
    alt: "The Workspaces page, showing workspace cards and the New workspace button.",
    caption: "Your workspaces. “New workspace” is at the top right.",
  },
  "lists": {
    alt: "A workspace opened, showing its lists as cards with actions on each.",
    caption: "Inside a workspace. Each list card carries share, move, duplicate, rename and delete.",
  },
  "columns": {
    alt: "The Columns tab of a list's settings, listing each column with its type.",
    caption: "Settings → Columns. Every column, its short name in brackets, and the buttons that change it.",
  },
  "add-column": {
    alt: "The Add column dialog: a name, an API name, a type, and that type's settings.",
    caption: "Adding a column. The settings under “Options” change with the type you pick.",
  },
  "change-type": {
    alt: "The Convert dialog for a column, offering the type to convert it into.",
    caption: "Converting a column. Nothing changes until you read the report and click Apply.",
  },
  "formula": {
    alt: "The Add column dialog with Calculated chosen, showing the Formula box and the columns it may name.",
    caption: "A calculated column holds a formula. The dialog lists the short names you are allowed to use.",
  },
  "records": {
    alt: "The Contacts list, showing records in a table.",
    caption: "A list with records in it — the screen you will spend most time on.",
  },
  "add-record": {
    alt: "The Add record dialog, with a field per column and required ones starred.",
    caption: "Adding a record. Required fields carry a star; the City box waits on Country.",
  },
  "nested-lookup": {
    alt: "The City picker open after France was chosen, offering only French cities.",
    caption: "City is nested under Country: choose France and it offers French cities only.",
  },
  "qr-menu": {
    alt: "The Add record split button with its menu open, offering the two QR options.",
    caption: "The arrow beside “Add record” opens the two QR options.",
  },
  "toolbar": {
    alt: "The toolbar above the records: view, columns, filter, group, sort, metrics and search.",
    caption: "The bar above your records. Everything here changes how you are looking, not what the list is.",
  },
  "filter": {
    alt: "The filter dialog, with one condition made of a column, an operator and a value.",
    caption: "A filter is one or more conditions, matched all or any.",
  },
  "views": {
    alt: "The view dropdown open, listing the saved views of a list.",
    caption: "Saved ways of looking. The “+” beside it makes a new one.",
  },
  "forms": {
    alt: "The Forms tab of a list's settings, with a row per field.",
    caption: "Settings → Forms. Tick what appears, tick what can be typed into, and reorder.",
  },
  "metrics": {
    alt: "The Metrics button turned on, and the three cards it reveals above the records.",
    caption: "Metrics, shown after clicking the Metrics button. They follow whatever is on screen.",
  },
  "sharing": {
    alt: "The permissions panel, listing who has access and what each may do.",
    caption: "Who can see this, and what they may do. Roles are shortcuts for the ticks beside them.",
  },
  "transfer": {
    alt: "The move-or-transfer dialog, offering another workspace or another person.",
    caption: "Move a list to another workspace, or hand it to somebody else.",
  },
  "duplicate": {
    alt: "The duplicate-list dialog, with the option to copy the records too.",
    caption: "Duplicating takes the structure; the records are a tick box.",
  },
  "import-export": {
    alt: "The import and export dialog, on its export tab.",
    caption: "Export picks a format, columns, and optionally a filter; import takes a file.",
  },
  "bin": {
    alt: "The deleted records dialog, with Restore and Delete permanently beside each row.",
    caption: "A bin sits beside everything you can delete. This is the one for records; Restore sits at the end of each row.",
  },
  "people": {
    alt: "The People page, listing accounts and groups.",
    caption: "Everyone with an account, and every group. Only an administrator sees the buttons that change them.",
  },
  "appearance": {
    alt: "The theme picker open, showing the colour themes and their swatches.",
    caption: "The two small icons by your name: light or dark, and which set of colours.",
  },
};
