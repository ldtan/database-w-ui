/**
 * Photographs the running app for the pictures in the guide.
 *
 *   npm run capture:guide
 *
 * Needs three things up: the backend on :5000, the dev server on :5173, and the
 * demo tenant seeded (`uv run python -m src.demo.guide` in ../backend). It signs
 * in as that tenant's owner, walks the app opening the things the guide describes,
 * and writes one PNG per shot into `public/guide/`, twice — `-light` and `-dark` —
 * because the guide renders in whichever mode the reader chose and a light
 * screenshot in a dark page looks like a hole in it.
 *
 * **Why it drives a real browser rather than mocking anything.** The point of the
 * pictures is that they are the app, so a screenshot has to be able to go wrong the
 * same way the app can. If a control here has been renamed, this run fails on the
 * selector — which is the alarm the guide's prose has never had (see the note at
 * the top of `src/features/guide/GuidePage.tsx`: it describes the UI and nothing
 * checks it). Every `SHOTS` entry is therefore a small assertion that a label the
 * guide names is still on screen.
 *
 * Adding a shot: add an entry to `SHOTS`, run this, then put a `<Figure>` in the
 * matching section of `GuidePage.tsx`. The `id` is the filename and the `<Figure>`
 * `name`; `section` is documentation, so a reader of this file can tell where a
 * picture ends up.
 *
 * The images are committed. They are build inputs, not build outputs — the guide
 * has to render for someone who has never run this script, and rebuilding them
 * requires a database.
 */
import { mkdir, readdir, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(HERE, "..", "public", "guide");
// The alt text and captions, as a module the guide imports. Generated rather than
// hand-kept beside the `<Figure>` tags, so the description of a picture cannot
// drift from the thing that took it.
const MANIFEST = join(HERE, "..", "src", "features", "guide", "figures.ts");

const BASE_URL = process.env.GUIDE_CAPTURE_URL ?? "http://localhost:5173";
// The demo tenant's owner. Matches `backend/src/demo/guide.py`; override both if
// you seeded with --password.
const USERNAME = process.env.GUIDE_CAPTURE_USER ?? "guide.demo";
const PASSWORD = process.env.GUIDE_CAPTURE_PASSWORD ?? "guidedemo2026";

// Wide enough for the rail, the page, and a 720px dialog with air around it, and
// short enough that a full-page shot is not a mile of scroll. Scaled ×2 so the
// text in a screenshot is as sharp as the text beside it.
const VIEWPORT = { width: 1400, height: 900 };
const SCALE = 2;

// Space left around a clipped element, in CSS pixels. Without it a dialog is cut
// flush at its own border and reads as a crop rather than as a thing on a page.
const PAD = 20;

// The narrowest a finished picture may be, before the ×2 scaling. The guide's
// column is 820px, so anything narrower than this arrives on the page enlarged —
// see `shoot`. Shots wider than it keep their own width. A few overlays are much
// smaller than a dialog and set their own, larger, value.
const MIN_FRAME = 900;

const MODES = ["light", "dark"];

// Shorter than Playwright's 30s default, because here a missing element is
// almost always a renamed control rather than a slow one — and forty shots each
// waiting half a minute to say so makes a run nobody sits through.
const SELECTOR_TIMEOUT = 8_000;

// Names the tenant uses, in one place: the seeder can rename a list without this
// file turning into a hunt for string literals.
const WORKSPACE = "Sales";
const LIST = "Contacts";

// --- plumbing -------------------------------------------------------------

/** Sign in against the API directly, for the uuids the script navigates by and
 *  for the token it hands the browser. Doing it here rather than through the form
 *  keeps the login screen a *subject* of a screenshot rather than a step in every
 *  run. */
async function apiLogin() {
  const form = new URLSearchParams({ username: USERNAME, password: PASSWORD });
  const res = await fetch(`${BASE_URL}/api/auth/tokens/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  if (!res.ok) {
    throw new Error(
      `Login as ${USERNAME} failed (${res.status}). Is the backend up, and has ` +
        `the demo been seeded? See backend/src/demo/guide.py.`,
    );
  }
  return res.json();
}

function apiGet(path, token) {
  return fetch(`${BASE_URL}/api${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  }).then(async (res) => {
    if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
    return res.json();
  });
}

async function apiPatch(path, token, body) {
  const res = await fetch(`${BASE_URL}/api${path}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PATCH ${path} -> ${res.status}`);
  return res.json();
}

/** The uuids every shot navigates by, resolved by display name so this file and
 *  the seeder agree on nouns rather than on ids. */
async function resolveTenant(token) {
  const workspaces = await apiGet("/workspaces/?limit=100", token);
  const workspace = workspaces.find((w) => w.display_name === WORKSPACE);
  if (!workspace) throw new Error(`No workspace named ${WORKSPACE}. Seed the demo.`);

  const lists = await apiGet(
    `/lists/?workspace_uuid=${workspace.uuid}&limit=100`,
    token,
  );
  const list = lists.find((l) => l.display_name === LIST);
  if (!list) throw new Error(`No list named ${LIST} in ${WORKSPACE}. Seed the demo.`);

  return { workspaceUuid: workspace.uuid, listUuid: list.uuid };
}

/** Everything a shot is handed. Keeping navigation on here rather than in each
 *  shot is what stops twenty entries each remembering to wait for the grid. */
function makeCtx(page, tenant) {
  const ctx = {
    page,
    ...tenant,

    async goto(path) {
      await page.goto(`${BASE_URL}${path}`, { waitUntil: "domcontentloaded" });
      await ctx.settle();
    },

    async home() {
      await ctx.goto("/");
    },

    async workspaces() {
      await ctx.goto("/workspaces");
      await page.getByText(WORKSPACE, { exact: true }).first().waitFor();
      await ctx.settle();
    },

    async workspace() {
      await ctx.goto(`/workspaces/${tenant.workspaceUuid}`);
      await page.getByText(LIST, { exact: true }).first().waitFor();
      await ctx.settle();
    },

    /** The Contacts list, with its records actually on screen. */
    async list() {
      await ctx.goto(`/lists/${tenant.listUuid}`);
      await page.getByText("Ada Lovelace").first().waitFor();
      await ctx.settle();
    },

    /** The list's settings dialog, open on one of its tabs. */
    async settings(tab) {
      await ctx.list();
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await dialog.waitFor();
      if (tab) await dialog.getByRole("tab", { name: tab, exact: true }).click();
      await ctx.settle();
      return dialog;
    },

    /** An item on the list page's overflow ("⋯") menu. */
    async moreMenu(item) {
      await page.getByRole("button", { name: "More list actions" }).click();
      await page.getByRole("menuitem", { name: item }).click();
      await ctx.settle();
    },

    /** An action on the first list card of a workspace page. A card shows one
     *  action inline (Share) and folds the rest behind a "⋮" — see RowActions —
     *  so everything interesting here is two clicks, not one. */
    async cardAction(label) {
      await ctx.workspace();
      // By name, not "the first card": the first is whichever list sorts first,
      // and a picture captioned with the guide's running example should be of it.
      const card = page.locator(".card-grid > div").filter({ hasText: LIST });
      await card.getByRole("button", { name: "More actions" }).click();
      await page.getByRole("menuitem", { name: label }).click();
      await ctx.settle();
    },

    /** Let React paint, queries land, and any transition finish. Animations are
     *  off (see `prepare`), so this is short on purpose. */
    async settle(ms = 350) {
      await page.waitForTimeout(ms);
    },
  };
  return ctx;
}

/**
 * Everything that makes two runs produce the same pixels: no animation, no caret
 * blinking, no help bubble in the corner of every picture.
 *
 * Installed as an init script rather than with `addStyleTag`, because a style tag
 * belongs to one document and every shot navigates at least once — the first run
 * of this had transitions back the moment a shot left the page it started on.
 */
const STEADY_CSS = `
  *, *::before, *::after {
    animation-duration: 0s !important;
    animation-delay: 0s !important;
    transition-duration: 0s !important;
    transition-delay: 0s !important;
    caret-color: transparent !important;
  }
  /* The help bubble follows you onto every page and would appear, uninvited, in
     the corner of half the pictures. It has a section of its own in the guide,
     which is where it should be seen. */
  .help-fab { visibility: hidden !important; }
`;

/** Grow a span to at least `wanted`, keeping it centred and inside `[0, limit]`. */
function widen(start, end, wanted, limit) {
  const short = wanted - (end - start);
  if (short <= 0) return [start, end];
  let from = start - short / 2;
  let to = end + short / 2;
  // Push back inside rather than clamping both ends, or a target near an edge
  // gets the growth only on the side it has room for and ends up off-centre.
  if (from < 0) [from, to] = [0, Math.min(limit, to - from)];
  if (to > limit) [from, to] = [Math.max(0, from - (to - limit)), limit];
  return [Math.round(from), Math.round(to)];
}

/**
 * Write one PNG, clipped to the union of `targets` plus a margin.
 *
 * A union rather than one element because the interesting thing is often a
 * control *and* the overlay it opened — a split button and its menu are two
 * elements in two stacking contexts, and a picture of either alone explains
 * nothing.
 *
 * **The frame is then widened to `MIN_FRAME`, which is the whole reason it is not
 * a plain `element.screenshot()`.** A crop cut flush around a 460px dialog and
 * then displayed across the guide's 820px column is a dialog magnified nearly
 * twice — its labels come out bigger than the sentences explaining them, which
 * reads as a mistake even when the picture is right. Framing every shot to a
 * common width means the thing photographed keeps the size it has on screen, and
 * a reader can see at a glance which controls are small and which are not.
 */
async function shoot(page, id, mode, targets, minFrame = MIN_FRAME) {
  const boxes = [];
  for (const target of [targets].flat()) {
    // Every match, not just the first: a locator that names three metric cards is
    // asking for a frame around the three of them.
    for (const element of await target.all()) {
      const box = await element.boundingBox();
      if (box) boxes.push(box);
    }
  }
  if (boxes.length === 0) throw new Error(`${id}: nothing visible to photograph`);

  const viewport = page.viewportSize();
  const [left, right] = widen(
    Math.max(0, Math.min(...boxes.map((b) => b.x)) - PAD),
    Math.min(viewport.width, Math.max(...boxes.map((b) => b.x + b.width)) + PAD),
    minFrame,
    viewport.width,
  );
  const top = Math.max(0, Math.min(...boxes.map((b) => b.y)) - PAD);
  const bottom = Math.min(
    viewport.height,
    Math.max(...boxes.map((b) => b.y + b.height)) + PAD,
  );

  await page.screenshot({
    path: join(OUT_DIR, `${id}-${mode}.png`),
    clip: { x: left, y: top, width: right - left, height: bottom - top },
  });
}

// --- the shots ------------------------------------------------------------

/**
 * One entry per picture in the guide.
 *
 * `run` positions the app and returns what to photograph: a locator, or several
 * to be framed together. `alt` is the image's alternative text, and `caption` is
 * printed under it — both are written here rather than in the guide so that the
 * thing which knows what it photographed is the thing that describes it.
 *
 * `minFrame` overrides `MIN_FRAME` for a shot whose subject is much smaller than
 * a dialog — a dropdown panel, a strip of small cards. Those want *more* of the
 * page around them, not less, or the picture is a close-up of three words.
 */
const SHOTS = [
  {
    id: "sign-in",
    section: "signing-in",
    alt: "The sign-in screen, with username and password boxes and a Sign in button.",
    caption: "The only page you see before signing in.",
    anonymous: true,
    async run(ctx) {
      await ctx.page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
      await ctx.page.getByRole("button", { name: "Sign in" }).waitFor();
      await ctx.settle();
      return ctx.page.locator(".login-card");
    },
  },
  {
    id: "shell",
    section: "getting-around",
    alt: "The app's home page, with the navigation strip down the left-hand side.",
    caption:
      "Home, with the strip on the left. It stays there wherever you go.",
    async run(ctx) {
      await ctx.home();
      return ctx.page.locator("body");
    },
  },
  {
    id: "nav-tree",
    section: "getting-around",
    alt: "The navigation strip, with workspaces expanded to show the lists inside.",
    caption:
      "Click the arrow beside a workspace to see its lists; click the name to open the workspace itself.",
    async run(ctx) {
      await ctx.home();
      // Sales by name, not "the first branch": the first is the Personal
      // workspace every account is given, and it is empty, so opening it
      // illustrated the sentence with the words "No lists yet".
      await ctx.page.getByRole("button", { name: `Expand ${WORKSPACE}` }).click();
      await ctx.settle();
      // Top of the rail down to the last thing in the tree. The rail itself is
      // the full height of the window, most of which is the empty space under a
      // short tree.
      return [
        ctx.page.locator(".app-brand"),
        ctx.page.locator('[data-tour="nav-rail"] a'),
      ];
    },
  },
  {
    id: "workspaces",
    section: "workspace",
    alt: "The Workspaces page, showing workspace cards and the New workspace button.",
    caption: "Your workspaces. “New workspace” is at the top right.",
    async run(ctx) {
      await ctx.workspaces();
      return ctx.page.locator("main");
    },
  },
  {
    id: "lists",
    section: "list",
    alt: "A workspace opened, showing its lists as cards with actions on each.",
    caption:
      "Inside a workspace. Each list card carries share, move, duplicate, rename and delete.",
    async run(ctx) {
      await ctx.workspace();
      return ctx.page.locator("main");
    },
  },
  {
    id: "columns",
    section: "columns",
    alt: "The Columns tab of a list's settings, listing each column with its type.",
    caption:
      "Settings → Columns. Every column, its short name in brackets, and the buttons that change it.",
    async run(ctx) {
      return ctx.settings("Columns");
    },
  },
  {
    id: "add-column",
    section: "columns",
    alt: "The Add column dialog: a name, an API name, a type, and that type's settings.",
    caption:
      "Adding a column. The settings under “Options” change with the type you pick.",
    async run(ctx) {
      const dialog = await ctx.settings("Columns");
      await dialog.getByRole("button", { name: "Add column" }).click();
      await ctx.settle();
      return ctx.page.getByRole("dialog").last();
    },
  },
  {
    id: "formula",
    section: "formulas",
    alt: "The Add column dialog with Calculated chosen, showing the Formula box and the columns it may name.",
    caption:
      "A calculated column holds a formula. The dialog lists the short names you are allowed to use.",
    async run(ctx) {
      const settings = await ctx.settings("Columns");
      await settings.getByRole("button", { name: "Add column" }).click();
      await ctx.settle();
      const dialog = ctx.page.getByRole("dialog").last();
      // Picking the type is what reveals the Formula box — the whole subject of
      // this picture — so this shot is worth the extra two clicks.
      await dialog.locator(".p-dropdown").first().click();
      await ctx.page
        .locator(".p-dropdown-panel li")
        .filter({ hasText: "Calculated" })
        .click();
      await ctx.settle();
      return dialog;
    },
  },
  {
    id: "records",
    section: "records",
    alt: "The Contacts list, showing records in a table.",
    caption: "A list with records in it — the screen you will spend most time on.",
    async run(ctx) {
      await ctx.list();
      return ctx.page.locator("main");
    },
  },
  {
    id: "add-record",
    section: "records",
    alt: "The Add record dialog, with a field per column and required ones starred.",
    caption:
      "Adding a record. Required fields carry a star; the City box waits on Country.",
    async run(ctx) {
      await ctx.list();
      await ctx.page.getByRole("button", { name: "Add record" }).click();
      await ctx.settle();
      return ctx.page.getByRole("dialog");
    },
  },
  {
    id: "nested-lookup",
    section: "nested-lookups",
    alt: "The City picker open after France was chosen, offering only French cities.",
    caption:
      "City is nested under Country: choose France and it offers French cities only.",
    async run(ctx) {
      await ctx.list();
      await ctx.page.getByRole("button", { name: "Add record" }).click();
      await ctx.settle();
      const dialog = ctx.page.getByRole("dialog");
      // The two lookup pickers in the order the seeder puts their columns in:
      // Country narrows City, so it has to be filled first — which is the whole
      // point being photographed.
      const pickers = dialog.locator(".p-autocomplete");
      const country = pickers.nth(0);
      const city = pickers.nth(1);

      await country.locator("button").click();
      await ctx.page.locator(".p-autocomplete-panel li").filter({ hasText: "France" }).click();
      await ctx.settle();

      await city.locator("button").click();
      await ctx.settle();
      // The whole dialog, not just the two fields. Framed to the fields alone the
      // picture was a horizontal band sliced across the middle of a dialog, with
      // the greyed-out table showing either side of it — which looks like a bug
      // rather than a close-up.
      return [dialog, ctx.page.locator(".p-autocomplete-panel")];
    },
  },
  {
    id: "qr-menu",
    section: "qr",
    // A button and a two-item menu. Widened until the page header it sits on is
    // in shot, so a reader can see *where* the arrow is rather than only what it
    // opens.
    minFrame: 1100,
    alt: "The Add record split button with its menu open, offering the two QR options.",
    caption:
      "The arrow beside “Add record” opens the two QR options.",
    async run(ctx) {
      await ctx.list();
      const split = ctx.page.locator(".p-splitbutton");
      await split.locator("button.p-splitbutton-menubutton").click();
      await ctx.settle();
      return [split, ctx.page.locator(".p-tieredmenu, .p-menu").last()];
    },
  },
  {
    id: "toolbar",
    section: "find",
    alt: "The toolbar above the records: view, columns, filter, group, sort, metrics and search.",
    caption:
      "The bar above your records. Everything here changes how you are looking, not what the list is.",
    async run(ctx) {
      await ctx.list();
      return ctx.page.locator('[data-tour="list-toolbar"]');
    },
  },
  {
    id: "filter",
    section: "find",
    alt: "The filter dialog, with one condition made of a column, an operator and a value.",
    caption: "A filter is one or more conditions, matched all or any.",
    async run(ctx) {
      await ctx.list();
      await ctx.page.getByRole("button", { name: "Filter" }).click();
      await ctx.settle();
      const dialog = ctx.page.getByRole("dialog");
      await dialog.getByRole("button", { name: "Add condition" }).click();
      await ctx.settle();
      return dialog;
    },
  },
  {
    id: "views",
    section: "views",
    minFrame: 1100,
    alt: "The view dropdown open, listing the saved views of a list.",
    caption: "Saved ways of looking. The “+” beside it makes a new one.",
    async run(ctx) {
      await ctx.list();
      const bar = ctx.page.locator('[data-tour="list-toolbar"]');
      await bar.locator(".p-dropdown").first().click();
      await ctx.settle();
      return [bar, ctx.page.locator(".p-dropdown-panel")];
    },
  },
  {
    id: "forms",
    section: "forms",
    alt: "The Forms tab of a list's settings, with a row per field.",
    caption:
      "Settings → Forms. Tick what appears, tick what can be typed into, and reorder.",
    async run(ctx) {
      return ctx.settings("Forms");
    },
  },
  {
    id: "metrics",
    section: "metrics",
    alt: "The Metrics button turned on, and the three cards it reveals above the records.",
    caption:
      "Metrics, shown after clicking the Metrics button. They follow whatever is on screen.",
    async run(ctx) {
      await ctx.list();
      await ctx.page.getByRole("button", { name: /^Metrics/ }).click();
      await ctx.settle();
      // The toolbar as well as the cards. Three small cards on their own framed
      // to the guide's width came out as a thin band floating in the middle of
      // nothing; with the button that revealed them in shot, the picture is the
      // sentence beside it.
      return [ctx.page.locator('[data-tour="list-toolbar"]'), ctx.page.locator(".metric-card")];
    },
  },
  {
    id: "sharing",
    section: "sharing",
    alt: "The permissions panel, listing who has access and what each may do.",
    caption:
      "Who can see this, and what they may do. Roles are shortcuts for the ticks beside them.",
    async run(ctx) {
      const dialog = await ctx.settings("Permissions");
      // The rows are what the caption promises, and they are below the fold of
      // the dialog's own scrollport — the form for adding somebody comes first.
      // Scrolled to the *last* row rather than to the heading, which is already
      // just inside the fold and so satisfies "if needed" without moving.
      await dialog.locator(".row-list-item").last().scrollIntoViewIfNeeded();
      await ctx.settle();
      return dialog;
    },
  },
  {
    id: "transfer",
    section: "transfer",
    alt: "The move-or-transfer dialog, offering another workspace or another person.",
    caption: "Move a list to another workspace, or hand it to somebody else.",
    async run(ctx) {
      await ctx.cardAction("Move or transfer list");
      return ctx.page.getByRole("dialog");
    },
  },
  {
    id: "duplicate",
    section: "duplicate",
    alt: "The duplicate-list dialog, with the option to copy the records too.",
    caption: "Duplicating takes the structure; the records are a tick box.",
    async run(ctx) {
      await ctx.cardAction("Duplicate list");
      return ctx.page.getByRole("dialog");
    },
  },
  {
    id: "import-export",
    section: "import-export",
    alt: "The import and export dialog, on its export tab.",
    caption: "Export picks a format, columns, and optionally a filter; import takes a file.",
    async run(ctx) {
      await ctx.list();
      await ctx.moreMenu("Import / Export");
      return ctx.page.getByRole("dialog");
    },
  },
  {
    id: "bin",
    section: "bin",
    alt: "The deleted records dialog, with Restore and Delete permanently beside each row.",
    caption:
      "A bin sits beside everything you can delete. This is the one for records; Restore sits at the end of each row.",
    async run(ctx) {
      await ctx.list();
      await ctx.moreMenu("Deleted records");
      return ctx.page.getByRole("dialog");
    },
  },
  {
    id: "people",
    section: "people",
    alt: "The People page, listing accounts and groups.",
    caption:
      "Everyone with an account, and every group. Only an administrator sees the buttons that change them.",
    async run(ctx) {
      await ctx.goto("/people");
      await ctx.page.getByText(USERNAME, { exact: true }).first().waitFor();
      await ctx.settle();
      return ctx.page.locator("main");
    },
  },
  {
    id: "appearance",
    section: "appearance",
    minFrame: 1100,
    alt: "The theme picker open, showing the colour themes and their swatches.",
    caption:
      "The two small icons by your name: light or dark, and which set of colours.",
    async run(ctx) {
      await ctx.home();
      const account = ctx.page.locator('[data-tour="nav-account"]');
      await ctx.page.getByRole("button", { name: "Choose theme" }).click();
      await ctx.settle();
      return [account, ctx.page.locator(".p-overlaypanel")];
    },
  },
];

/** The manifest the guide reads, written from `SHOTS` in full on every run —
 *  including a partial one, since it describes the set of pictures rather than
 *  the set just taken. */
async function writeManifest() {
  const entries = SHOTS.map(
    (shot) =>
      `  "${shot.id}": {\n` +
      `    alt: ${JSON.stringify(shot.alt)},\n` +
      `    caption: ${JSON.stringify(shot.caption)},\n` +
      `  },`,
  ).join("\n");

  await writeFile(
    MANIFEST,
    `/**
 * What each picture in the guide shows — generated, do not edit.
 *
 * Written by \`scripts/capture-guide.mjs\` from its own \`SHOTS\` list, which is
 * also what took the images in \`public/guide/\`. Run \`npm run capture:guide\` to
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
${entries}
};
`,
    "utf8",
  );
}

// --- the run --------------------------------------------------------------

async function main() {
  const only = process.argv.slice(2).filter((a) => !a.startsWith("-"));
  const shots = only.length
    ? SHOTS.filter((s) => only.includes(s.id))
    : SHOTS;
  if (only.length && shots.length !== only.length) {
    const known = SHOTS.map((s) => s.id).join(", ");
    throw new Error(`Unknown shot id. Known ids: ${known}`);
  }

  const { access_token: token, refresh_token: refresh } = await apiLogin();
  const tenant = await resolveTenant(token);

  await mkdir(OUT_DIR, { recursive: true });
  await writeManifest();
  // Only when capturing everything: a partial run must not delete the pictures it
  // is not retaking.
  if (!only.length) {
    for (const name of await readdir(OUT_DIR)) {
      if (name.endsWith(".png")) await rm(join(OUT_DIR, name));
    }
  }

  const browser = await chromium.launch();
  const failures = [];

  try {
    for (const mode of MODES) {
      // Through the account rather than by poking the DOM, because `ThemeSync`
      // would reconcile a DOM-level change straight back to what the server holds.
      await apiPatch("/auth/users/me/settings", token, { theme: "modern", mode });

      const context = await browser.newContext({
        viewport: VIEWPORT,
        deviceScaleFactor: SCALE,
        colorScheme: mode,
      });
      context.setDefaultTimeout(SELECTOR_TIMEOUT);
      // Signed in before the first paint, so no shot has to step around a login
      // form — and the pre-paint theme script finds the right mode already there.
      // The onboarding record goes in for the same reason: the welcome dialog is
      // modal and shown once per account per device, and a fresh browser context
      // is a fresh device every time. Without this it sat over the first shot of
      // every run and its mask swallowed the clicks of all the others.
      await context.addInitScript(
        ([access, refreshToken, m, user, css]) => {
          localStorage.setItem("dbui.access", access);
          localStorage.setItem("dbui.refresh", refreshToken);
          localStorage.setItem("ui-theme", "modern");
          localStorage.setItem("ui-mode", m);
          localStorage.setItem(
            "dbui.onboarding.v1",
            JSON.stringify({
              [user]: { welcomed: true, seen: ["basics", "workspace", "list"] },
            }),
          );
          document.addEventListener("DOMContentLoaded", () => {
            const style = document.createElement("style");
            style.textContent = css;
            document.head.append(style);
          });
        },
        [token, refresh, mode, USERNAME, STEADY_CSS],
      );

      for (const shot of shots) {
        const page = await context.newPage();
        try {
          await page.emulateMedia({ reducedMotion: "reduce" });
          const ctx = makeCtx(page, tenant);
          // The one shot that is *of* being signed out has to actually be signed
          // out, or it photographs a login form with a session behind it.
          if (shot.anonymous) {
            await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
            await page.evaluate(() => {
              localStorage.removeItem("dbui.access");
              localStorage.removeItem("dbui.refresh");
            });
          }
          const targets = await shot.run(ctx);
          await shoot(page, shot.id, mode, targets, shot.minFrame);
          process.stdout.write(`  ${shot.id} (${mode})\n`);
        } catch (error) {
          failures.push(`${shot.id} (${mode}): ${error.message}`);
          process.stdout.write(`  ${shot.id} (${mode}) — FAILED\n`);
        } finally {
          await page.close();
        }
      }
      await context.close();
    }
  } finally {
    await browser.close();
    // Leave the demo account as the seeder set it, so a re-seed and a re-capture
    // are not two different starting states.
    await apiPatch("/auth/users/me/settings", token, {
      theme: "modern",
      mode: "light",
    });
  }

  if (failures.length) {
    process.stdout.write(`\n${failures.length} shot(s) failed:\n`);
    for (const failure of failures) process.stdout.write(`  ${failure}\n`);
    process.stdout.write(
      "\nA failed selector usually means a control was renamed. Fix the shot, and " +
        "check whether GuidePage.tsx still describes the same button.\n",
    );
    process.exitCode = 1;
  } else {
    process.stdout.write(`\n${shots.length * MODES.length} images in public/guide/\n`);
  }
}

await main();
