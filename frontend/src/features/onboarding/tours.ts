/**
 * The guided tours, and the per-page tips behind the help button.
 *
 * This file is the whole of the *content*; the components that show it know
 * nothing about workspaces or records. Adding a step is adding an entry here and
 * a `data-tour` attribute to the thing it points at.
 *
 * Three tours rather than one long one. A tour that walked from the rail all the
 * way into a record would have to invent a workspace and a list to walk through,
 * and a brand-new account has neither — so `basics` covers only what exists the
 * moment you sign in (the shell, the workspaces page, the guide), and the two
 * page tours are offered by the help button when you are somewhere they apply.
 *
 * The voice is the guide's: plain words, no jargon, and the on-screen labels
 * spelled exactly as they are on screen.
 *
 * **This file goes stale silently.** It quotes labels and points at elements
 * that nothing type-checks, and a step whose element has gone degrades to a
 * centred card rather than failing — so a UI change leaves it confidently wrong
 * and CI green. Any change to the shell, to a page's controls, or to the steps
 * of a flow means re-reading this and `guide/GuidePage.tsx` alongside it. The
 * full contract is under "Gotchas for whoever edits this next" in
 * `docs/known-gaps.md`.
 */

export type TourId = "basics" | "workspace" | "list";

export interface TourStep {
  /**
   * The `data-tour` value of the element to point at. Omitted for a step that is
   * about an idea rather than a control, which renders centred instead.
   *
   * A step whose element is not on the page — a card grid with no cards yet —
   * falls back to that same centred card, so every step's wording has to make
   * sense without the thing it points at.
   */
  anchor?: string;
  /** Where this step lives. The tour goes there before showing it; omitted means
   *  "wherever you already are", which is how the page tours work. */
  path?: string;
  title: string;
  body: string;
  /** A short list under the body, for a step that is really three small facts. */
  points?: string[];
  /** Steps that only make sense at one width: the rail exists above 1024px, and
   *  below it the same tree is folded behind the header's burger. */
  width?: "wide" | "narrow";
}

export interface Tour {
  id: TourId;
  /** How the help button offers it. */
  label: string;
  icon: string;
  blurb: string;
  steps: TourStep[];
}

const basics: Tour = {
  id: "basics",
  label: "Show me around",
  icon: "pi pi-compass",
  blurb: "A two-minute look at the app and how to start.",
  steps: [
    {
      path: "/",
      anchor: "dash-hero",
      title: "This is home",
      body: "You land here every time you sign in. It counts up what you have, keeps your most recent workspaces within one click, and points at the handful of places worth going next.",
    },
    {
      path: "/",
      anchor: "nav-rail",
      width: "wide",
      title: "Your map, always on the left",
      body: "This strip stays put while you work, so everything you have is visible from wherever you are. Home, Workspaces, People, and the Guide — the long written version of this tour.",
    },
    {
      path: "/",
      anchor: "nav-toggle",
      width: "narrow",
      title: "Your map lives here",
      body: "This window is too narrow for a permanent strip down the side, so the same map folds away behind this button. Tap it for your workspaces and the lists inside them, your account, and the way out.",
    },
    {
      path: "/",
      anchor: "nav-workspaces",
      width: "wide",
      title: "Your workspaces, and what is in them",
      body: "Each workspace is listed by name. Two targets, on purpose — the small arrow opens the branch to show the lists inside, and the name opens the workspace itself.",
      points: [
        "Click a list in the tree to go straight to it.",
        "Shared with me, at the bottom, holds lists people gave you directly.",
      ],
    },
    {
      path: "/",
      anchor: "nav-account",
      width: "wide",
      title: "You, down at the bottom",
      body: "Your initials and your name. Click for your profile or to log out; the two small icons beside it switch between light and dark and between colour themes. Your choice is saved to your account, so it follows you to another computer.",
    },
    {
      path: "/workspaces",
      anchor: "new-workspace",
      title: "Step one: make a workspace",
      body: "A workspace is a folder that keeps related lists together — a Sales workspace, say. Give it a name and it appears below as a card. That is the only thing standing between you and your first list.",
    },
    {
      path: "/workspaces",
      anchor: "workspace-card",
      title: "Step two: open it, and add a list",
      body: "Every workspace you make shows up as a card like this. Open one and click New list — a list is the actual table, the heart of the app. Then, inside the list: Settings → Columns to decide what each record holds, and Add record to put something in it.",
      points: [
        "Column — one detail every record has, like Email or Status.",
        "Record — one row: a single contact, task or item.",
        "A list needs at least one column before it will take records.",
      ],
    },
    {
      path: "/guide",
      anchor: "guide-contents",
      title: "Everything else is written down",
      body: "The Guide is the whole app in plain language — sharing, filters, forms, formulas, QR codes, and getting back things you deleted. Twenty-odd sections, and this list jumps to any of them.",
    },
    {
      path: "/",
      anchor: "help-button",
      title: "And this button never goes away",
      body: "It is here on every page. It has tips for whatever you are looking at, it will run this tour again whenever you like, and there are two more tours behind it — one for a workspace, one for a list — that appear when you are on those pages.",
    },
  ],
};

const workspace: Tour = {
  id: "workspace",
  label: "Tour this workspace",
  icon: "pi pi-th-large",
  blurb: "Lists, and the four things you can do to one.",
  steps: [
    {
      anchor: "new-list",
      title: "A list is the table itself",
      body: "Name it after the things it will hold — Contacts, Orders, Equipment. It starts empty and without columns of its own, so the first thing to do inside it is Settings → Columns.",
    },
    {
      anchor: "list-card",
      title: "Everything you can do to a list",
      body: "Each card opens its list, and the buttons on it act on the list without opening it. On a narrow card they collapse into a ⋯ menu, and anything you are not allowed to do is not shown at all.",
      points: [
        "Share — give a person or a group access, exactly as much as you choose.",
        "Duplicate — copy the columns, and the records too if you tick the box.",
        "Move or transfer — send it to another workspace, or hand it to someone.",
      ],
    },
    {
      anchor: "deleted-lists",
      title: "Deleting is not the end",
      body: "A deleted list goes to a bin you can open here and restore it from, with its records intact. It is kept for a while rather than forever, so fetch anything important back sooner than later.",
    },
  ],
};

const list: Tour = {
  id: "list",
  label: "Tour this list",
  icon: "pi pi-table",
  blurb: "Columns, records, and finding things again.",
  steps: [
    {
      anchor: "list-settings",
      title: "Start here: what a record holds",
      body: "Settings is where the list is shaped rather than filled. Columns comes first — a list with none cannot take records yet.",
      points: [
        "Columns — add Text, Number, Date, Choice, a link to another list, or a formula.",
        "Forms — choose which fields appear when someone adds a record, and in what order.",
        "Permissions — who can see this list, and what they may do to it.",
      ],
    },
    {
      anchor: "add-record",
      title: "Then fill it in",
      body: "One row at a time. Fields marked with a star are required, and an empty cell shows as a dash rather than a blank. Click a record later to edit it.",
      points: [
        "The small arrow beside it fills the form from a QR code, or saves a record per scan — built for working through a queue of badges.",
      ],
    },
    {
      anchor: "list-toolbar",
      title: "Finding things once there are lots",
      body: "This bar changes what you are looking at, never what the list is. Search covers the whole list, not just the page you are on; sort, group and filter narrow it down.",
      points: [
        "None of it sticks on its own — save an arrangement into a view to keep it.",
        "A view remembers its filter, sort, grouping and columns, and the dropdown flips between them.",
      ],
    },
    {
      anchor: "list-more",
      title: "The occasional things",
      body: "Import brings a CSV or JSON file in, matching your columns by their short names; export takes records out, filtered and sorted however you like. Deleted records is this list's own bin.",
    },
  ],
};

export const TOURS: Record<TourId, Tour> = { basics, workspace, list };

/** The page tour that applies where you are standing, if there is one. */
export function pageTourFor(pathname: string): Tour | null {
  if (/^\/lists\/[^/]+$/.test(pathname)) return list;
  // The workspaces index has no page tour of its own; `basics` covers it.
  if (/^\/workspaces\/[^/]+$/.test(pathname)) return workspace;
  return null;
}

export interface PageTip {
  icon: string;
  text: string;
}

/**
 * The "on this page" lines in the help panel — the two or three things worth
 * knowing about where you are, for somebody who will not read the guide.
 */
export function tipsFor(pathname: string): PageTip[] {
  if (/^\/lists\/[^/]+$/.test(pathname)) {
    return [
      { icon: "pi pi-cog", text: "No columns yet? Settings → Columns. A list cannot take records without at least one." },
      { icon: "pi pi-filter", text: "Search, sort and filter change only your view — click Save view to keep an arrangement." },
      { icon: "pi pi-lock", text: "A button you cannot see is one you are not allowed to use. Ask the list's owner." },
    ];
  }
  if (/^\/workspaces\/[^/]+$/.test(pathname)) {
    return [
      { icon: "pi pi-table", text: "A list is the table. Open one and its columns come first — they decide what a record holds." },
      { icon: "pi pi-copy", text: "Duplicate copies the columns and leaves the records behind, unless you tick the box — a quick way to make a template." },
      { icon: "pi pi-send", text: "Move or transfer sends a list to another workspace, or hands it to another person for good." },
    ];
  }
  if (pathname === "/workspaces") {
    return [
      { icon: "pi pi-th-large", text: "A workspace is just a folder for lists. Make one before anything else." },
      { icon: "pi pi-share-alt", text: "Share a workspace and everything in it comes along — share a single list to be narrower." },
      { icon: "pi pi-inbox", text: "Shared with me holds lists given to you directly; they live in somebody else's workspace." },
    ];
  }
  if (pathname === "/people") {
    return [
      { icon: "pi pi-users", text: "Groups are for sharing with a whole team at once instead of person by person." },
      { icon: "pi pi-globe", text: "Authenticated Users means everyone with an account here, including people who join later." },
      { icon: "pi pi-shield", text: "Only an administrator can add accounts or change them. Everyone else can look." },
    ];
  }
  if (pathname === "/profile") {
    return [
      { icon: "pi pi-user", text: "Your name here is where the initials in your avatar come from. Filling it in is optional." },
      { icon: "pi pi-key", text: "Changing your password asks for the current one too." },
    ];
  }
  if (pathname === "/guide") {
    return [
      { icon: "pi pi-list", text: "The contents list at the top jumps straight to any section." },
      { icon: "pi pi-book", text: "It follows one example — a Contacts list — from the first workspace to sharing and formulas." },
    ];
  }
  return [
    { icon: "pi pi-th-large", text: "Workspace → list → column → record. Everything here is those four words in that order." },
    { icon: "pi pi-compass", text: "Nothing yet? Take the tour above — it walks you as far as your first list." },
    { icon: "pi pi-palette", text: "The two icons by your name change the theme, and the choice follows your account to any device." },
  ];
}
