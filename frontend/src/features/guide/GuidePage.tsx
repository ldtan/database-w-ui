import { useEffect, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { Button } from "primereact/button";

import { Breadcrumbs } from "../../components/Breadcrumbs";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import { useOptionalOnboarding } from "../onboarding/OnboardingProvider";
import { Figure } from "./Figure";
import { SECTIONS } from "./sections";

/**
 * A plain-language, start-to-finish guide to the app, written for people with no
 * technical background. It is a static document — no data is fetched — so it works
 * the moment someone signs in and never depends on having built anything yet.
 *
 * Because it fetches nothing, it is also the one page that reads correctly to
 * somebody who has not signed in, and a deployment with `HOME_ENABLED` serves it to
 * them from the public home page. `standalone` is that case: no breadcrumb back into
 * an app the reader is not in, and no tour button, because a tour walks the real
 * screens and there is no session to walk them with. Everything else is identical —
 * one document, not two, which is the point.
 *
 * The ids in `sections.ts` are the anchor targets of the contents list at the top.
 * SECTIONS and the <Section> ids have to stay in step — the list is generated from
 * it — and `guide.test.ts` now fails when they don't, along with a figure named
 * here that nobody captured and an in-page link to a section that isn't there.
 *
 * It names controls in prose, and nothing checks that they still exist or are
 * still called that — this page once spent a release describing a header that
 * had become a sidebar. A UI change is not finished until this and
 * `onboarding/tours.ts` have been re-read; see "Gotchas for whoever edits this
 * next" in `docs/known-gaps.md`. `COLUMN_TYPE_OPTIONS` below is the pattern
 * where it can be avoided: the type list is generated rather than retyped.
 *
 * The `<Figure>`s are the other half of that pattern, and the stronger half:
 * each is a screenshot taken by `scripts/capture-guide.mjs` driving the real app,
 * so `npm run capture:guide` fails on a control that has been renamed instead of
 * leaving a paragraph confidently describing one that is gone. Re-run it after a
 * UI change; the pictures are committed, and are what a reader sees.
 */

/** One visible on-screen label (a button, menu item, or field name), styled so it
 *  stands out from the surrounding prose — it maps a step to something the reader
 *  can actually spot on the screen. */
function Ui({ children }: { children: ReactNode }) {
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 text-sm font-medium"
      style={{
        background: "var(--surface-2)",
        border: "var(--border-width) solid var(--border)",
        color: "var(--text)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

/** A snippet of formula text, set in monospace so it reads as something to type
 *  literally rather than as prose. */
function Code({ children }: { children: ReactNode }) {
  return (
    <code
      className="rounded px-1 text-sm"
      style={{
        background: "var(--surface-2)",
        color: "var(--text)",
        fontFamily: "monospace",
      }}
    >
      {children}
    </code>
  );
}

/** Every formula function on one row each, for looking one up rather than reading
 *  about it. The prose above it teaches; this is what you come back to. */
const FUNCTIONS: {
  name: string;
  params: string;
  what: string;
  example: string;
  gives: string;
}[] = [
  // Numbers
  { name: "MIN", params: "a, b, …", what: "The smallest of the values given.", example: 'MIN(quoted, agreed)', gives: "the lower price" },
  { name: "MAX", params: "a, b, …", what: "The largest of the values given.", example: "MAX(0, total - paid)", gives: "never below 0" },
  { name: "ROUND", params: "number, places?", what: "Rounds off. Leave the places out for a whole number.", example: "ROUND(total, 2)", gives: "12.35" },
  { name: "ABS", params: "number", what: "The value without its minus sign.", example: "ABS(counted - expected)", gives: "3" },
  { name: "INT", params: "text or number", what: "Reads a whole number out of text, dropping anything after the point.", example: 'INT("42.7")', gives: "42" },
  // Words
  { name: "CONCAT", params: "a, b, …", what: "Sticks pieces together. Numbers are welcome.", example: 'CONCAT(first, " ", last)', gives: "Ada Lovelace" },
  { name: "UPPER", params: "text", what: "The same words in capitals.", example: "UPPER(code)", gives: "AB12" },
  { name: "LOWER", params: "text", what: "The same words in small letters.", example: "LOWER(email)", gives: "ada@x.com" },
  { name: "TRIM", params: "text", what: "Removes stray spaces from either end.", example: 'TRIM("  hi  ")', gives: "hi" },
  { name: "LEN", params: "text", what: "How many characters it has, as a number.", example: 'LEN("hello")', gives: "5" },
  { name: "LEFT", params: "text, n", what: "The first n characters.", example: "LEFT(surname, 1)", gives: "L" },
  { name: "RIGHT", params: "text, n", what: "The last n characters.", example: "RIGHT(phone, 4)", gives: "7788" },
  { name: "REPLACE", params: "text, find, with", what: "Swaps every occurrence of one bit of text for another.", example: 'REPLACE(ref, "-", "/")', gives: "AB/12" },
  // Dates
  { name: "AGE", params: "date", what: "How many whole years have passed. Negative for a date still to come.", example: "AGE(birthday)", gives: "36" },
  { name: "DT_FORMAT", params: "date, pattern", what: "Writes a date out however you like — see the pattern pieces above.", example: 'DT_FORMAT(due, "%d %b %Y")', gives: "06 Aug 2026" },
  { name: "TODAY", params: "—", what: "Today’s date. Takes nothing, so the brackets stay empty.", example: 'IF(due < TODAY(), "Overdue", "Open")', gives: "Overdue" },
  { name: "NOW", params: "—", what: "The date and time right now. For a Date and time column.", example: 'IF(starts <= NOW(), "Running", "Not yet")', gives: "Running" },
  // Choosing
  { name: "IF", params: "condition, then, otherwise", what: "The second value when the condition holds, the third when it doesn’t.", example: 'IF(qty > 100, "Bulk", "Standard")', gives: "Bulk" },
  { name: "IS_NULL", params: "value", what: "1 when there’s nothing there, 0 when there is. One of the two things that can look at an empty cell.", example: "IF(IS_NULL(discount), price, price - discount)", gives: "the full price" },
  { name: "DEFAULT", params: "value, fallback", what: "The value, or the second one when it’s empty. The short way to fill a gap.", example: 'DEFAULT(nickname, full_name)', gives: "Ada" },
];

/** The reference table itself. Scrolls sideways on a narrow screen rather than
 *  pushing the page out of shape. */
function FunctionTable() {
  return (
    <div style={{ overflowX: "auto", maxWidth: "100%" }}>
      <table
        className="text-sm"
        style={{ borderCollapse: "collapse", minWidth: 640, width: "100%" }}
      >
        <thead>
          <tr style={{ borderBottom: "var(--border-width) solid var(--border)" }}>
            {["Name", "What goes in it", "What it does", "Example"].map((h) => (
              <th
                key={h}
                className="font-semibold"
                style={{ textAlign: "left", padding: "0.5rem 0.75rem 0.5rem 0" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {FUNCTIONS.map((fn) => (
            <tr
              key={fn.name}
              style={{ borderBottom: "var(--border-width) solid var(--border)" }}
            >
              <td style={{ padding: "0.5rem 0.75rem 0.5rem 0", verticalAlign: "top" }}>
                <Code>{fn.name}</Code>
              </td>
              <td
                style={{
                  padding: "0.5rem 0.75rem 0.5rem 0",
                  verticalAlign: "top",
                  color: "var(--text-muted)",
                  whiteSpace: "nowrap",
                }}
              >
                {fn.params}
              </td>
              <td style={{ padding: "0.5rem 0.75rem 0.5rem 0", verticalAlign: "top" }}>
                {fn.what}
              </td>
              <td style={{ padding: "0.5rem 0 0.5rem 0", verticalAlign: "top" }}>
                <Code>{fn.example}</Code>
                <div style={{ color: "var(--text-muted)", paddingTop: "0.2rem" }}>
                  → {fn.gives}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A titled section with a stable id so the contents list can jump to it.
 *
 *  `guide-section` carries the scroll offset (themes.css): the heading is a jump
 *  target, and on a narrow screen the sticky app header would otherwise cover the
 *  one the reader just asked for.
 *
 *  It ends with a link back to the contents, because this page is long enough that
 *  the list at the top is off-screen for all but the first section or two, and
 *  scrolling back up through twenty-odd sections to reach it is the only route
 *  there otherwise. `topLink={false}` for the first section, where the list is
 *  already a few lines above. */
function Section({
  id,
  title,
  children,
  topLink = true,
}: {
  id: string;
  title: string;
  children: ReactNode;
  topLink?: boolean;
}) {
  return (
    <section id={id} className="guide-section flex flex-col gap-3">
      <h2
        className="text-xl font-semibold pt-2"
        style={{ borderTop: "var(--border-width) solid var(--border)" }}
      >
        {title}
      </h2>
      {children}
      {topLink && (
        <div className="text-sm" style={{ textAlign: "right" }}>
          <a
            href="#guide-contents"
            aria-label="Back to contents"
            style={{ color: "var(--text-muted)", textDecoration: "none" }}
          >
            ↑ Contents
          </a>
        </div>
      )}
    </section>
  );
}

/** A highlighted aside — a tip, or a "watch out for this" note. */
function Callout({
  tone = "tip",
  title,
  children,
}: {
  tone?: "tip" | "warn";
  title: string;
  children: ReactNode;
}) {
  const color = tone === "warn" ? "var(--accent)" : "var(--brand)";
  return (
    <div
      className="rounded p-3 flex flex-col gap-1"
      style={{
        background: "var(--brand-tint)",
        borderLeft: `3px solid ${color}`,
      }}
    >
      <div className="font-semibold flex items-center gap-2">
        <i className={tone === "warn" ? "pi pi-exclamation-triangle" : "pi pi-lightbulb"} />
        {title}
      </div>
      <div className="text-sm" style={{ color: "var(--text)" }}>
        {children}
      </div>
    </div>
  );
}

/** Plain-language description for each column type, keyed by its stored value. The
 *  labels themselves come from the same list the "Add column" menu uses, so they
 *  can never drift from what the app actually offers. */
const TYPE_HELP: Record<string, string> = {
  text: "Writing — a name, a title, a reference number. Choose how it is filled in: one line, several lines for notes or an address, or rich text, which gives you bold, italic, lists, quotes and links. You can change that later without disturbing what is already stored.",
  number: "A number you can total or compare. You can set a smallest and largest allowed value, or require whole numbers only.",
  checkbox: "A simple yes/no, on/off tick — “Paid?”, “Active?”.",
  choice: "A fixed set of options you pick from, like Todo, Doing, Done — so everyone uses the same wording. Can allow one option or several. You can also allow an “Other” answer, which adds a box for anything the options don’t cover.",
  date: "A calendar day, with no time of day.",
  time: "A time of day, with no date.",
  datetime: "A day and a time together.",
  link: "A web address that becomes clickable. You can show friendly words in place of the address.",
  email: "An e-mail address, checked as you save it and tidied into one spelling — Ada@Example.com and ada@example.com are stored as the same address, so a search for one finds the other and Unique catches the duplicate.",
  phone: "A phone number. Pick the country and type it however you like — 0917 123 4567, +63 917 123 4567 — and it is stored one way, so the same number typed two ways is one number. Set a default country if most of your numbers are local; without one, every number has to carry its own country code.",
  location: "A place: an address, a point on a map, or both. Search for the address, or drop a pin — and a cell shows a link out to a map. Filtering and sorting use the address, which is the half a person can read.",
  color: "A colour, picked from a swatch or typed as a hex code like #3366ff. You can offer a palette of your own; it suggests, it doesn't restrict, because the point of a colour field is picking one.",
  rating: "A score out of five — or out of whatever you set, up to ten — shown as stars, hearts or a plain number. An empty cell means unrated, so ratings start at 1.",
  duration: "A length of time — how long a lesson ran, hours worked. Type it as 1:30 or 90m and choose how it is shown (1:30, 1:30:00, 90:00 or 1.5 hours). It totals and averages like a number, so “Total hours” is a metric you can add.",
  user: "A pointer to a person who has an account here. Can allow one person or several.",
  group: "A pointer to a group of people. Can allow one group or several.",
  lookup: "A link to a row in another list — one of yours, in any of your workspaces, or one someone shared with you — for example, linking an order to a customer. One lookup can also narrow another, so picking a country leaves only its cities to choose from. It can also let people add a missing row to the other list without leaving the form they are filling in.",
  calculated: "A value worked out automatically from your other columns using a formula, such as qty * price. You never type into it — see “Calculated columns: writing a formula” below.",
};

/**
 * Land on the section a `#fragment` names, when the reader *arrives* at the guide
 * already asking for one.
 *
 * The contents list at the top of this page has never needed it: those are same-page
 * fragment anchors, which the browser handles by itself. What does need it is a link
 * from somewhere else — the home page points at `#columns`, `#views`, `#sharing` —
 * and neither way of arriving works on its own. A client-side navigation does not
 * scroll at all, because React Router's declarative router has no opinion about
 * fragments. A cold load does scroll, and then lands nowhere: this page carries
 * twenty-five `loading="lazy"` figures with no reserved height, so the document grows
 * by thousands of pixels *after* the browser has already jumped. Measured, before
 * this existed: a click from the home page to `#sharing` left the reader 21,000px
 * above it.
 *
 * Hence the retries. One pass is the jump; the later ones are the correction after
 * the figures around the target have resolved and moved it. They stop the moment the
 * reader touches the page — a scroll of their own means they have taken over, and
 * yanking them back would be worse than landing imprecisely.
 *
 * The honest fix is for a figure to reserve its space, which means its dimensions in
 * `figures.ts` — a change to `capture-guide.mjs` and a re-run against a seeded
 * database. Worth doing; this is what works without one.
 *
 * One editing note, since this file is checked as *text*: do not write a literal
 * fragment anchor in a comment here. `guide.test.ts` scans the whole source for them
 * and would read it as a link to a section that does not exist.
 */
function useHashLanding() {
  const { hash } = useLocation();

  useEffect(() => {
    if (!hash) return;
    const id = decodeURIComponent(hash.slice(1));
    if (!document.getElementById(id)) return;

    let cancelled = false;
    const land = () => {
      if (!cancelled) document.getElementById(id)?.scrollIntoView();
    };
    // `scrollIntoView` honours `scroll-margin-top`, which is what keeps the heading
    // clear of the sticky header — see `.guide-section` in themes.css.
    land();
    const timers = [60, 250, 600].map((ms) => window.setTimeout(land, ms));

    const stop = () => {
      cancelled = true;
    };
    const options = { passive: true, once: true } as const;
    window.addEventListener("wheel", stop, options);
    window.addEventListener("touchstart", stop, options);
    window.addEventListener("keydown", stop, { once: true });

    return () => {
      cancelled = true;
      timers.forEach((t) => window.clearTimeout(t));
      window.removeEventListener("wheel", stop);
      window.removeEventListener("touchstart", stop);
      window.removeEventListener("keydown", stop);
    };
  }, [hash]);
}

export function GuidePage({ standalone = false }: { standalone?: boolean }) {
  // Null outside the authenticated shell — see `useOptionalOnboarding`. Called
  // unconditionally, as a hook must be; what is conditional is the button below.
  const onboarding = useOptionalOnboarding();
  useHashLanding();

  return (
    <div className="flex flex-col gap-6" style={{ maxWidth: 820 }}>
      {!standalone && <Breadcrumbs items={[{ label: "Guide" }]} />}

      <header className="flex flex-col gap-3">
        <h1 className="text-2xl font-semibold">How to use this app</h1>
        <p style={{ color: "var(--text-muted)" }}>
          A friendly, step-by-step guide — from your very first workspace to
          sharing, filtering, and formulas. No technical background needed. If you
          can use a spreadsheet, you can use this.
        </p>
        {/* For the people who would rather be shown than read. The tour is the
            first quarter of this page, pointed at the real controls — which is why
            it is offered only to a reader who has a session to point it at. */}
        {onboarding && (
          <div>
            <Button
              label="Show me around instead"
              icon="pi pi-compass"
              outlined
              onClick={() => onboarding.startTour("basics")}
            />
          </div>
        )}
      </header>

      {/* Contents */}
      {/* `id` is the target every section's "↑ Contents" link points at; the
          `data-tour` attribute is separate and load-bearing — a step of the
          `basics` tour spotlights this nav by it. Neither replaces the other. */}
      <nav
        id="guide-contents"
        className="guide-contents surface-card p-4"
        aria-label="Guide contents"
        data-tour="guide-contents"
      >
        <div className="font-semibold mb-2">On this page</div>
        {/* The whole list in one column is most of a screen before the guide has
            started. Two columns on anything but a phone. */}
        <ol
          className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {SECTIONS.map((s, i) => (
            <li key={s.id}>
              <a
                href={`#${s.id}`}
                style={{ color: "var(--brand)", textDecoration: "none" }}
              >
                <span style={{ color: "var(--text-muted)" }}>{i + 1}.</span>{" "}
                {s.title}
              </a>
            </li>
          ))}
        </ol>
      </nav>

      <Callout tone="tip" title="We’ll build one thing together">
        As we go, imagine you’re building a simple <strong>Contacts</strong> list —
        people’s names, emails, and whether you’ve reached out. Every step below uses
        it as an example, so the buttons you read about are the ones you’d really
        click.
      </Callout>

      <Section id="what-it-is" title="What this app is" topLink={false}>
        <p>
          This app lets you build your own little databases through a screen —
          no code, no spreadsheets to email around. Think of it as a tidy,
          shareable set of tables that live in one place, where you decide what each
          table holds and who is allowed to see or change it.
        </p>
        <p>
          People use it for things like a contact book, an inventory, a task
          tracker, a guest list, a simple CRM — anything that is really “a table of
          things, each with the same set of details.”
        </p>
      </Section>

      <Section id="words" title="Five words to know">
        <p>The whole app is built from five simple ideas:</p>
        <ul className="flex flex-col gap-2">
          <li>
            <strong>Workspace</strong> — a folder that holds related lists (for
            example, a “Sales” workspace).
          </li>
          <li>
            <strong>List</strong> — a single table (like “Contacts”). This is the
            heart of the app.
          </li>
          <li>
            <strong>Column</strong> — one kind of detail every row records (like
            “Email” or “Status”). You choose what <em>type</em> of information each
            column holds.
          </li>
          <li>
            <strong>Record</strong> — one row: a single contact, task, or item, with
            a value in each column.
          </li>
          <li>
            <strong>View</strong> — a saved way of looking at a list: which rows
            show, how they’re sorted and grouped, and which columns appear.
          </li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          If you know spreadsheets: a list is a sheet, a column is a column, and a
          record is a row — with the app keeping the rules tidy for you.
        </p>
        <p style={{ color: "var(--text-muted)" }}>
          Two more words turn up later, once you have records to look at:{" "}
          <strong>forms</strong> shape the screen people fill in, and{" "}
          <strong>metrics</strong> are the running totals above a list. Both have
          their own sections below; neither is something you need on day one.
        </p>
      </Section>

      <Section id="signing-in" title="Signing in">
        <p>
          There is no public sign-up. An administrator creates your account and gives
          you a <strong>username</strong> and <strong>password</strong>. On the
          sign-in screen, type them in and click <Ui>Sign in</Ui>. That’s it — you
          land on your home page.
        </p>
        <Figure name="sign-in" />
        <p>
          If you don’t have an account yet, ask whoever runs this app for one (see{" "}
          <a href="#people" style={{ color: "var(--brand)" }}>
            People, groups, and your profile
          </a>
          ).
        </p>
      </Section>

      <Section id="getting-around" title="Getting around">
        <p>
          Once you’re in, the strip down the left-hand side is your map. It stays
          there while you work, so you can always see everything you have:
        </p>
        <Figure name="shell" />
        <ul className="flex flex-col gap-2">
          <li>
            The name at the top (<Ui>Database w/ UI</Ui>) always takes you back to
            your <Link to="/" style={{ color: "var(--brand)" }}>home dashboard</Link>.
          </li>
          <li>
            Then four places to go: <Ui>Home</Ui>, <Ui>Workspaces</Ui> (all your
            folders), <Ui>People</Ui> (accounts and groups), and <Ui>Guide</Ui>,
            which brings you here.
          </li>
          <li>
            Under <strong>Workspaces</strong>, each of your workspaces is listed by
            name. Click the small arrow beside one to open it up and see the lists
            inside, then click a list to go straight to it — no need to go through
            the workspace first. Clicking the <em>name</em> opens the workspace
            itself.
          </li>
          <li>
            <strong>Shared with me</strong>, at the bottom of that tree, holds lists
            other people have given you directly. They live in someone else’s
            workspace, so this is the only place they appear.
          </li>
          <li>
            At the very bottom is <strong>your account</strong> — a circle with your
            initials, and your name beside it. Click it to reach <Ui>Profile</Ui>{" "}
            (your own details) or <Ui>Log out</Ui>. The two small icons next to it
            switch between light and dark and between colour themes — see{" "}
            <a href="#appearance" style={{ color: "var(--brand)" }}>Appearance</a>.
          </li>
          <li>
            The trail of links above each page’s title (the <em>breadcrumbs</em>)
            shows where you are and lets you step back up.
          </li>
          <li>
            The round <Ui>?</Ui> button in the bottom right corner is on every
            page. It holds two or three tips for whatever you are looking at, a
            link back to this guide, and the <strong>tours</strong> — see below.
          </li>
        </ul>
        <Figure name="nav-tree" />
        <Callout tone="tip" title="Rather be shown than read?">
          A tour dims the page and points at one thing at a time, in the app
          itself. <Ui>Show me around</Ui> is the general one — the rail, your
          workspaces, and where to make your first list — and there are two more
          that appear only where they apply: one on a workspace page, one on a
          list. All three live behind the <Ui>?</Ui> button, and you can leave any
          of them part-way with <Ui>Esc</Ui> and pick it up again later.
        </Callout>
        <p>
          Your <Link to="/" style={{ color: "var(--brand)" }}>home page</Link> is a
          dashboard: how many workspaces you have and how many lists other people
          have shared with you, then cards for your most recent workspaces and those
          shared lists, so you can jump straight back into what you were doing.
          Underneath, <Ui>Explore</Ui> links to the main corners of the app.
        </p>
        <Callout tone="tip" title="On a phone or a narrow window?">
          There isn’t room for the strip beside the page, so it folds away behind
          the ☰ button at the top left. Tap it and the same map slides out — your
          workspaces, their lists, and everywhere else — with your account and{" "}
          <Ui>Log out</Ui> at the top of it. The circle at the top right of the bar
          does the same job as the one at the bottom of the strip.
        </Callout>
      </Section>

      <Section id="workspace" title="Step 1 · Make a workspace">
        <p>
          A workspace is just a folder to keep related lists together. Let’s make one
          to hold our Contacts list.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Go to <Ui>Workspaces</Ui> (from the strip on the left, or the card on
            your home page).
          </li>
          <li>
            Click <Ui>New workspace</Ui> at the top right.
          </li>
          <li>
            Type a name — say <em>Sales</em> — and click <Ui>Create</Ui>. Your new
            workspace appears as a card below.
          </li>
          <li>Click the card to open it.</li>
        </ol>
        <Figure name="workspaces" />
        <Callout tone="tip" title="Two names, one thing">
          Each workspace (and list) has an everyday <Ui>Name</Ui> and a shorter{" "}
          <Ui>API name</Ui>. You only ever need the everyday name — the app fills in
          the short one for you. The short one matters only to people connecting other
          software, and changing it later can break those connections, so leave it be
          unless you know you need to.
        </Callout>
      </Section>

      <Section id="list" title="Step 2 · Make a list">
        <p>Inside your workspace, a list is the actual table.</p>
        <ol className="flex flex-col gap-2">
          <li>Open your <em>Sales</em> workspace.</li>
          <li>
            Click <Ui>New list</Ui>, type <em>Contacts</em>, and click{" "}
            <Ui>Create</Ui>.
          </li>
          <li>Click the list’s card to open it.</li>
        </ol>
        <Figure name="lists" />
        <p>
          A brand-new list has no records and none of your own columns yet — so the
          next step is to decide what details each contact should have.
        </p>
      </Section>

      <Section id="columns" title="Step 3 · Add columns">
        <p>
          Columns are the details every record will fill in. You set these up in the
          list’s settings.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            With your list open, click <Ui>Settings</Ui>, then the <Ui>Columns</Ui>{" "}
            tab.
          </li>
          <li>Click <Ui>Add column</Ui>.</li>
          <li>
            Give it a name (e.g. <em>Full name</em>) and pick a <Ui>Type</Ui> from
            the menu (for a name, <Ui>Text</Ui>).
          </li>
          <li>
            Fill in any extra settings that appear for that type, then click{" "}
            <Ui>Add</Ui>.
          </li>
        </ol>
        <Figure name="columns" />
        <Figure name="add-column" />
        <p>For our Contacts list, you might add:</p>
        <ul className="flex flex-col gap-1">
          <li>
            <em>Full name</em> — <Ui>Text</Ui>, and tick <Ui>Required</Ui>{" "}
            so no contact is nameless.
          </li>
          <li>
            <em>Email</em> — <Ui>Text</Ui>, and tick <Ui>Unique</Ui> so
            the same address can’t be added twice.
          </li>
          <li>
            <em>Status</em> — <Ui>Choice</Ui> with options{" "}
            <em>New; Contacted; Customer</em>.
          </li>
          <li>
            <em>Birthday</em> — <Ui>Date</Ui>.
          </li>
        </ul>
        <p>Along the way you can also:</p>
        <ul className="flex flex-col gap-1">
          <li>
            Make a column <Ui>Required</Ui> (a record can’t be saved without it) or{" "}
            <Ui>Unique</Ui> (no two records may share the value).
          </li>
          <li>
            Give it a <Ui>Default value</Ui> that fills in automatically when left
            blank.
          </li>
          <li>
            Hold a value inside a range — <Ui>Min</Ui> and <Ui>Max</Ui> on a number,{" "}
            <Ui>Earliest</Ui> and <Ui>Latest</Ui> on a date, time or date &amp; time.
            Fill in one and the column is bounded on that side only.
          </li>
          <li>
            Have date, time, or user columns stamp themselves automatically when a
            record is created or edited.
          </li>
          <li>
            Let a <Ui>Choice</Ui> column take an <Ui>Other</Ui> answer, for the cases
            your options don’t cover — see below.
          </li>
        </ul>
        <Callout tone="tip" title="Four columns are already there">
          Every list starts with <Ui>Created at</Ui>, <Ui>Updated at</Ui>,{" "}
          <Ui>Created by</Ui> and <Ui>Updated by</Ui>. The app fills them in for you
          — when the record was made and last changed, and by whom — so nobody types
          into them, and they’re kept out of the add/edit screen. They still show in
          the table, where you can hide them like any column, and you can switch them
          on in a form (see{" "}
          <a href="#forms" style={{ color: "var(--brand)" }}>Forms</a>) if you’d like
          people to see them while editing.
        </Callout>
        <Callout tone="tip" title="Changing your mind later">
          You can rename a column and change its settings any time. Changing its{" "}
          <strong>type</strong> is a bigger job — every record already holds values in
          the old shape — so it has its own button and its own safety net, described
          in{" "}
          <a href="#change-type" style={{ color: "var(--brand)" }}>
            Changing a column’s type
          </a>
          . And deleting a column doesn’t erase what records held — the old values are
          simply hidden.
        </Callout>
      </Section>

      <Section id="column-types" title="The column types, explained">
        <p>
          When you add a column, the <Ui>Type</Ui> menu offers these. Pick the one
          that matches the kind of information you’re storing:
        </p>
        <dl className="flex flex-col gap-3">
          {COLUMN_TYPE_OPTIONS.map((t) => (
            <div key={t.value} className="flex flex-col gap-0.5">
              <dt className="font-semibold">{t.label}</dt>
              <dd className="m-0 text-sm" style={{ color: "var(--text-muted)" }}>
                {TYPE_HELP[t.value] ?? ""}
              </dd>
            </div>
          ))}
        </dl>
        <h3 className="font-semibold pt-1">When your options don’t cover it</h3>
        <p>
          A <Ui>Choice</Ui> column can allow an <Ui>Other</Ui> answer. Tick{" "}
          <Ui>Allow an “Other” answer</Ui> when you set the column up, and the menu
          gains an <em>Other…</em> entry that opens a box to type in. Useful when you
          know most of the answers but not all of them — a <em>Reason for leaving</em>,
          say, or a <em>Referred by</em>.
        </p>
        <p>
          What gets typed is stored exactly as typed, the same way an option is, so
          those answers sort, filter and export like everything else. The flip side is
          that the app can’t tell that <em>Referred by a friend</em> and{" "}
          <em>referred by friend</em> were meant to be the same thing — they’re two
          answers. If you find yourself getting the same write-in over and over, add it
          to the options.
        </p>
        <Callout tone="warn" title="Turning it back off">
          Records that already hold a write-in keep it, and it still shows and exports.
          But the column no longer accepts it, so the next person to edit one of those
          records has to pick from the list. Nothing is rewritten behind your back.
        </Callout>
        <h3 className="font-semibold pt-1">Picking more than one</h3>
        <p>
          Tick <Ui>Allow selecting multiple options</Ui> and the column takes a list
          rather than a single answer — programmes a student is enrolled in, tags on
          a ticket. The cell becomes a picker you add to and remove from, and the grid
          shows the values separated by commas.
        </p>
        <Callout tone="warn" title="Filtering a multi-answer column">
          <Ui>is</Ui> asks for that <em>exact set</em>, so it rarely matches what you
          meant. Use <Ui>contains</Ui> to find records holding a particular option —
          bearing in mind it matches part of a word too, so <em>Arts</em> also finds{" "}
          <em>Martial Arts</em>.
        </Callout>
        <Callout tone="warn" title="Going back to a single answer">
          Records keep only their first value from then on. The rest are still there
          for a while — switching back on brings them back — but they go for good the
          next time that record is edited. The app asks you to confirm before saving.
        </Callout>
        <Callout tone="tip" title="Start simple">
          Text, Number, Checkbox, Choice, and Date cover almost everything. The
          people-, link-, lookup-, and formula-style columns are powerful extras you
          can reach for later — they’re covered under{" "}
          <a href="#sharing" style={{ color: "var(--brand)" }}>sharing</a>,{" "}
          <a href="#nested-lookups" style={{ color: "var(--brand)" }}>
            linked columns
          </a>
          , and{" "}
          <a href="#formulas" style={{ color: "var(--brand)" }}>formulas</a>.
        </Callout>
      </Section>

      <Section
        id="change-type"
        title="Changing a column’s type, and copying a column"
      >
        <p>
          Stored a year as <Ui>Text</Ui> and now want to total it? Typed dates by
          hand and want a real date picker? You don’t have to start over. In{" "}
          <Ui>Settings</Ui> → <Ui>Columns</Ui>, each column has two buttons beside
          the pencil:
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            <Ui>Change type</Ui> (the arrows icon) — converts this column, keeping
            its name and everything that points at it.
          </li>
          <li>
            <Ui>Duplicate</Ui> (the copy icon) — makes a <em>second</em> column from
            this one and leaves the original alone. It can keep the same type (a
            straight copy) or take a new one.
          </li>
        </ul>

        <h3 className="font-semibold pt-1">Nothing changes until you say so</h3>
        <p>
          Both work the same way, and it’s worth knowing why: the app does a{" "}
          <strong>trial run</strong> first and shows you exactly what would and
          wouldn’t convert. That trial writes nothing at all — while you’re reading
          the report your list is untouched, and every view, filter and form still
          sees the old column, working normally. Only when you click <Ui>Apply</Ui>{" "}
          (or <Ui>Add column</Ui>, for a copy) does anything change, and then it all
          changes at once. <Ui>Cancel</Ui> costs you nothing.
        </p>
        <p style={{ color: "var(--text-muted)" }}>
          Because it happens in one go, a busy list is worth converting when nobody
          else is typing into it: if a record changes while the conversion is running,
          the app stops and undoes the whole thing rather than half-converting your
          list. Try again and it goes through.
        </p>
        <Figure name="change-type" />
        <p>
          If some values can’t be converted — a <em>“n/a”</em> sitting in a column
          you’re turning into <Ui>Number</Ui> — the report names them. Those cells end
          up <strong>empty</strong>. You can discard, fix them, and try again.
        </p>

        <h3 className="font-semibold pt-1">Which types can become which</h3>
        <p>
          <Ui>Text</Ui> is the go-between. Anything can become text, and text can
          become almost anything, so a swap the menu greys out is simply one that
          takes two steps: convert to <Ui>Text</Ui> first, then on to what you wanted.
          Going from <Ui>Date</Ui> to <Ui>Date &amp; time</Ui> (or back) works
          directly. <Ui>Calculated</Ui> columns can’t take part at all: giving one a
          formula, or taking its formula away, is a decision about the column rather
          than a conversion of what’s in it.
        </p>
        <p>
          Turning <Ui>Text</Ui> into <Ui>Choice</Ui> is the clever one: leave the
          options blank and the app reads your records and offers one option per
          distinct value it finds. Type the options yourself instead and anything
          outside your list is reported as a failure rather than quietly added —
          unless you also tick <Ui>Allow an “Other” answer</Ui>, which is usually what
          you want here: the options say what people should pick from now on, and
          what’s already written stays put.
        </p>

        <Callout tone="tip" title="Your old values are kept">
          Converting doesn’t throw the originals away. They’re moved into a deleted
          column named something like <em>price__before_conversion</em>, which you can
          bring back from{" "}
          <a href="#bin" style={{ color: "var(--brand)" }}>Deleted columns</a> if the
          conversion wasn’t what you hoped. Duplicating displaces nothing at all, so
          there’s nothing to recover.
        </Callout>
        <Callout tone="warn" title="Required and Unique are checked at the end">
          A report can come back with no failures and the change still be refused —
          because <Ui>Required</Ui> and <Ui>Unique</Ui> are judged on the finished
          column, not cell by cell. If some records would be left empty in a required
          column, or two would end up sharing a value in a unique one, you’ll be told
          then. Your conversion stays waiting, so you can untick the setting or fix
          the records and apply again.
        </Callout>
      </Section>

      <Section id="records" title="Step 4 · Add records">
        <p>Now the fun part — putting real contacts in.</p>
        <ol className="flex flex-col gap-2">
          <li>
            With the list open, click <Ui>Add record</Ui>.
          </li>
          <li>
            Fill in the fields. Any marked with a star (<strong>*</strong>) are
            required.
          </li>
          <li>
            Click <Ui>Add</Ui>. Your new record shows up in the table.
          </li>
        </ol>
        <Figure name="add-record" />
        <Figure name="records" />
        <p>To change a contact later, open it, edit the fields, and click <Ui>Save</Ui>.</p>
        <ul className="flex flex-col gap-1">
          <li>An empty field shows as a dash (—).</li>
          <li>A ticked checkbox shows a check mark; an unticked one shows a cross.</li>
          <li>
            A web-address field becomes a clickable link (opening in a new tab) only
            when the address is valid; otherwise it stays as plain text.
          </li>
          <li>
            On a phone, records appear as tidy <strong>cards</strong> — one contact
            per card, label beside value — instead of a wide table. You can do
            everything from a card that you can from the table.
          </li>
        </ul>
        <Callout tone="warn" title="Add a column first">
          If a list has no columns yet, the add screen just says{" "}
          <Ui>Add a column first.</Ui> Set up at least one column (previous step)
          before adding records.
        </Callout>
        <Callout tone="tip" title="Other people’s changes arrive on their own">
          You don’t need to refresh. While you have a list open, records that
          somebody else adds, edits, archives or deletes appear within a second or
          so — and so do changes you make in another tab on another device. If a row
          seems to change under you, that is usually why rather than anything you
          did. On a connection that won’t hold the live link open, the list falls
          back to refreshing itself every so often instead, so it still catches up,
          just less promptly.
        </Callout>
      </Section>

      <Section
        id="nested-lookups"
        title="Linked columns that narrow each other"
      >
        <p>
          A <Ui>Lookup (link to another list)</Ui> column normally offers every
          row of the list it points at. When two of those columns belong
          together — a{" "}
          <em>Country</em> and a <em>City</em> on the same record — you can{" "}
          <strong>nest</strong> one under the other, so choosing the first decides
          what the second may offer. Pick <em>France</em>, and the city box only
          suggests French cities.
        </p>
        <p>
          <strong>What you need first.</strong> Three lists, in a chain: a{" "}
          <em>Countries</em> list, a <em>Cities</em> list that itself has a link
          column pointing at <em>Countries</em>, and the list you’re really filling
          in (say <em>Contacts</em>) with a link to each. That middle column — the
          one on <em>Cities</em> saying which country a city is in — is what makes
          the narrowing possible.
        </p>
        <p>
          <strong>Setting it up.</strong> In the list you’re filling in, go to{" "}
          <Ui>Settings</Ui> → <Ui>Columns</Ui> and add or edit the <em>City</em>{" "}
          column:
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Pick the <Ui>Workspace</Ui> that holds <em>Cities</em> (the current one
            by default, or <Ui>Shared with me</Ui> for a list someone shared), then
            set the <Ui>Target list</Ui> to <em>Cities</em>, as with any link column.
          </li>
          <li>
            Under <Ui>Nest under (optional)</Ui>, choose <em>Country</em> — the
            column on <em>this</em> list whose choice should do the narrowing.
          </li>
          <li>
            Under <Ui>Match on</Ui>, choose the column on <em>Cities</em> that says
            which country each city belongs to. If only one column fits, the app
            picks it for you.
          </li>
          <li>Click <Ui>Save</Ui> (or <Ui>Add</Ui>).</li>
        </ol>
        <p>
          <strong>What people see when adding a record.</strong> The <em>City</em>{" "}
          box starts switched off, reading{" "}
          <Ui>Select Country first</Ui>. Choose a country and it wakes up,
          suggesting only that country’s cities. Change the country later and the
          city clears itself — the old city belonged to the old country.
        </p>
        <Figure name="nested-lookup" />
        <p>
          <strong>Longer chains work too.</strong> Nest <em>Country</em> under{" "}
          <em>Continent</em> and <em>City</em> under <em>Country</em>, and the
          boxes light up one after another as you go. Changing something near the
          top clears everything below it.
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            <Ui>Nest under</Ui> only appears when a sensible pairing exists. If you
            can’t see it, the target list has no column pointing back at the other
            column’s list — add that link column there first.
          </li>
          <li>
            To undo the nesting, set <Ui>Nest under</Ui> back to{" "}
            <Ui>Not nested</Ui>. The column keeps its values and simply offers
            everything again.
          </li>
          <li>
            The rule is enforced wherever records are saved — typing, importing a
            file, or filling from a QR code — so a city that doesn’t sit in the
            chosen country is refused with a message saying so.
          </li>
          <li>
            Filtering and searching are deliberately <em>not</em> narrowed: there,
            you’re looking across the whole list, not filling in one record.
          </li>
        </ul>
        <Callout tone="tip" title="Turning it on later is safe">
          Nesting an existing column doesn’t break records already saved, even ones
          whose pairing no longer matches. Nothing is checked until someone edits
          that particular field — so you can tidy old rows at your own pace.
        </Callout>

        <h3 className="font-semibold pt-1">
          Letting people add a missing row as they go
        </h3>
        <p>
          The usual snag with a link column is the row that isn’t there yet: someone
          filling in a contact needs the city <em>Cebu</em>, it has never been added
          to <em>Cities</em>, and they have to abandon what they were doing, go to
          the other list, add it, and come back. Ticking{" "}
          <Ui>Let people add a new row while filling this in</Ui> on the link column
          removes that trip — searching for something that doesn’t exist offers to
          create it, right there in the picker.
        </p>
        <p>
          <strong>It is judged person by person, not once when you set it up.</strong>{" "}
          The box being ticked doesn’t grant anybody anything. Each person filling in
          the form is checked against their own access to the target list: someone
          who can already add rows to <em>Cities</em> gets the offer, and someone who
          can’t simply sees the ordinary search, with no broken button and no error.
          So turning it on is safe even when the people using the form have a mix of
          access — including on a list that was only shared with you as a link target.
        </p>
        <Callout tone="warn" title="Only in the picker">
          This is a convenience in the form, not a new rule. Importing a file or
          using the API with a name that doesn’t exist still fails the same way it
          always did — nothing is created on your behalf there.
        </Callout>
      </Section>

      <Section id="formulas" title="Calculated columns: writing a formula">
        <p>
          A <Ui>Calculated (formula)</Ui> column doesn’t hold anything you type. It
          holds a <strong>formula</strong>, and its value is worked out from the
          other columns of the same record. Change a <em>Quantity</em>, and the{" "}
          <em>Total</em> beside it is already right — there is nothing to refresh and
          nothing to keep in step. The answer is saved with the record too, which is
          what lets you sort, filter and search by it like any other column.
        </p>
        <p>
          Add one under <Ui>Settings</Ui> → <Ui>Columns</Ui> → <Ui>Add column</Ui>,
          pick the type, and type the formula into the <Ui>Formula</Ui> box. The
          dialog lists, underneath, the columns you’re allowed to name.
        </p>
        <Figure name="formula" />

        <h3 className="font-semibold pt-1">Naming your columns</h3>
        <p>
          A formula refers to a column by its <strong>short name</strong> — the one
          shown in brackets beside it in the columns list, like{" "}
          <Code>unit_price</Code> — not by its pretty display name. These kinds of
          column can be named: <strong>Number</strong>, <strong>Rating</strong>,{" "}
          <strong>Duration</strong>, <strong>Text</strong> (any input style),{" "}
          <strong>Email</strong>, <strong>Choice</strong>, <strong>Date</strong>,{" "}
          <strong>Date &amp; time</strong>, and another <strong>Calculated</strong>{" "}
          column — so a <em>Total</em> can build on a <em>Subtotal</em>, as long as
          the two don’t end up depending on each other.
        </p>
        <p style={{ color: "var(--text-muted)" }}>
          The rest can’t be used in a formula, each for its own reason: a tick box
          and a colour have no wording to work with; web links, phone numbers, places
          and times of day aren’t things you calculate over; and people, groups and
          linked columns hold a pointer to a row somewhere else rather than the words
          you see.
        </p>
        <p style={{ color: "var(--text-muted)" }}>
          A <strong>Choice</strong> column reads as the option itself, so{" "}
          <Code>IF(status == "Done", 1, 0)</Code> does what it looks like. Where
          several options can be picked at once, they read as one piece of text with
          commas between them — <em>Robotics, Choir</em>.
        </p>

        <h3 className="font-semibold pt-1">Doing sums</h3>
        <p>
          Use <Code>+</Code>, <Code>-</Code>, <Code>*</Code> (times),{" "}
          <Code>/</Code> (divided by) and brackets, exactly as you would on paper:{" "}
          <Code>(qty * price) - discount</Code>. These work on numbers only — to
          join words together see <Code>CONCAT</Code> below.
        </p>
        <p style={{ color: "var(--text-muted)" }}>
          Function names are always typed in <strong>CAPITALS</strong>, and column
          names are always lower case — so you can tell at a glance which is which,
          and a column called <Code>total</Code> never gets mistaken for anything
          else.
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>MIN(a, b, …)</Code> — the smallest of the values given.
          </li>
          <li>
            <Code>MAX(a, b, …)</Code> — the largest.
          </li>
          <li>
            <Code>ROUND(number)</Code> or <Code>ROUND(number, places)</Code> —{" "}
            <Code>ROUND(total, 2)</Code> gives you pence or cents.
          </li>
          <li>
            <Code>ABS(number)</Code> — the value without its minus sign.
          </li>
          <li>
            <Code>INT(text)</Code> — reads a number out of a piece of text, and
            drops anything after the decimal point. <Code>INT("42.7")</Code> is{" "}
            <Code>42</Code> and <Code>INT("-3.9")</Code> is <Code>-3</Code>. Handy
            when a code or a reference number was typed into a text column and you
            want to do sums with it; text that isn’t a number at all gives no
            answer.
          </li>
        </ul>

        <h3 className="font-semibold pt-1">Working with words</h3>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>CONCAT(a, b, …)</Code> — sticks pieces together.{" "}
            <Code>CONCAT(first_name, " ", last_name)</Code> gives{" "}
            <em>Ada Lovelace</em>. Numbers are welcome too:{" "}
            <Code>CONCAT("$", price)</Code>.
          </li>
          <li>
            <Code>UPPER(text)</Code> / <Code>LOWER(text)</Code> — the same words in
            capitals, or all in small letters.
          </li>
          <li>
            <Code>TRIM(text)</Code> — removes stray spaces from either end.
          </li>
          <li>
            <Code>LEN(text)</Code> — how many characters it has, as a number.
          </li>
          <li>
            <Code>LEFT(text, n)</Code> / <Code>RIGHT(text, n)</Code> — the first, or
            last, <em>n</em> characters. <Code>LEFT(surname, 1)</Code> gives an
            initial.
          </li>
          <li>
            <Code>REPLACE(text, find, replace_with)</Code> — swaps every occurrence
            of one bit of text for another.
          </li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          Text you type into a formula yourself goes in quotes —{" "}
          <Code>"Ms. "</Code> — so the app can tell it apart from a column name.
        </p>

        <h3 className="font-semibold pt-1">Working with dates</h3>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>AGE(date)</Code> — how many <strong>whole years</strong> have
            passed, as a number. <Code>AGE(birthday)</Code> is how old someone is
            today, counting a birthday that hasn’t come round yet as the year
            before. A date in the future comes out negative, so{" "}
            <Code>IF(AGE(starts) &lt; 0, "Not yet", "Started")</Code> works.
          </li>
          <li>
            <Code>DT_FORMAT(date, "pattern")</Code> — writes a date out however you
            like. <Code>DT_FORMAT(enrolled, "%d %b %Y")</Code> gives{" "}
            <em>06 Aug 2026</em>.
          </li>
          <li>
            <Code>TODAY()</Code> and <Code>NOW()</Code> — today’s date, and this
            moment. They take nothing, so the empty brackets are part of writing
            them. Compare them against a date column the way you would compare two
            columns: <Code>IF(due &lt; TODAY(), "Overdue", "Open")</Code>, or{" "}
            <Code>IF(starts &lt;= NOW(), "Running", "Not yet")</Code>. Use{" "}
            <Code>TODAY()</Code> against a <strong>Date</strong> column and{" "}
            <Code>NOW()</Code> against a <strong>Date and time</strong> one — they
            answer in the same shape as the column they belong with. They go inside
            the other date functions too: <Code>DT_FORMAT(TODAY(), "%A")</Code> is
            what day of the week it is.
          </li>
        </ul>
        <p>The pieces you can put in a pattern:</p>
        <ul className="flex flex-col gap-1">
          <li>
            <strong>Year</strong> — <Code>%Y</Code> (2026), <Code>%y</Code> (26).
          </li>
          <li>
            <strong>Month</strong> — <Code>%m</Code> (08), <Code>%B</Code>{" "}
            (August), <Code>%b</Code> (Aug).
          </li>
          <li>
            <strong>Day</strong> — <Code>%d</Code> (06), <Code>%A</Code>{" "}
            (Thursday), <Code>%a</Code> (Thu), <Code>%j</Code> (218, the day of the
            year).
          </li>
          <li>
            <strong>Time</strong> — <Code>%H</Code> (00–23), <Code>%I</Code> (01–12)
            with <Code>%p</Code> (AM/PM), <Code>%M</Code> minutes,{" "}
            <Code>%S</Code> seconds.
          </li>
          <li>
            <Code>%%</Code> if you want a literal percent sign. Everything else in
            the pattern is copied through as you typed it.
          </li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          A plain <strong>Date</strong> column has no time on it, so the time pieces
          read as midnight. Anything after a <Code>%</Code> that isn’t in the list
          above isn’t understood — the app tells you so when you save, rather than
          leaving you a column full of blanks.
        </p>
        <p style={{ color: "var(--text-muted)" }}>
          <Code>AGE</Code>, <Code>TODAY()</Code> and <Code>NOW()</Code> are the
          things here that change on their own: the same formula gives a different
          answer tomorrow. What you see is always worked out fresh, so the answer on
          screen is right. Sorting and filtering read a stored copy that catches up
          overnight, so on somebody’s birthday — or the day a task falls due — they
          can be a step behind. Press <Ui>Refresh calculated</Ui> in the columns
          panel if that matters. A column built on <Code>NOW()</Code> feels this
          most: it is right on screen and worth sorting on only just after a save,
          so keep it for something you read rather than something you order by.
        </p>

        <h3 className="font-semibold pt-1">Choosing between two answers</h3>
        <p>
          <Code>IF(condition, then, otherwise)</Code> gives the second value when
          the condition holds and the third when it doesn’t:{" "}
          <Code>IF(qty &gt; 100, "Bulk", "Standard")</Code>, or{" "}
          <Code>IF(paid &gt;= total, 0, total - paid)</Code>.
        </p>
        <p>
          Conditions compare with <Code>&gt;</Code>, <Code>&lt;</Code>,{" "}
          <Code>&gt;=</Code>, <Code>&lt;=</Code>, <Code>==</Code> (is exactly) and{" "}
          <Code>!=</Code> (is not), and you can join them with <Code>and</Code>,{" "}
          <Code>or</Code> and <Code>not</Code>. Note the{" "}
          <strong>double</strong> equals sign for “is equal to”.
        </p>

        <h3 className="font-semibold pt-1">When a cell is empty</h3>
        <p>
          A formula that needs a value it hasn’t got gives no answer at all: leave{" "}
          <em>Discount</em> blank and <Code>price - discount</Code> is blank too,
          rather than quietly treating the gap as a zero. An incomplete record
          should look incomplete.
        </p>
        <p>
          When you’d rather fill the gap in, <Code>DEFAULT(x, fallback)</Code> is
          the shortest way to say it: the value, or the second thing when the first
          is empty.
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>DEFAULT(nickname, full_name)</Code> — the first of these that has
            been filled in.
          </li>
          <li>
            <Code>DEFAULT(discount, 0)</Code> — no discount means nothing off, so{" "}
            <Code>price - DEFAULT(discount, 0)</Code> gives a figure on every row
            instead of blanking the ones nobody discounted.
          </li>
          <li>
            <Code>DEFAULT(nickname, DEFAULT(first_name, "friend"))</Code> — work
            down a list until something has been filled in.
          </li>
          <li>
            <Code>DEFAULT(notes, "—")</Code> — a dash where a cell is blank, so the
            column reads as answered rather than unfinished.
          </li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          It also covers a sum that can’t be worked out, which is the tidy way to
          head off dividing by nothing: <Code>DEFAULT(total / qty, 0)</Code>. And it
          only reaches for the fallback when it has to, so{" "}
          <Code>DEFAULT(total, price / qty)</Code> is safe on a row where{" "}
          <em>Quantity</em> is nothing.
        </p>
        <p>
          The other one is <Code>IS_NULL(x)</Code>, for when you want to{" "}
          <em>ask</em> about an empty cell rather than fill it in. It gives{" "}
          <Code>1</Code> when there’s nothing there and <Code>0</Code> when there
          is:
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>IS_NULL(email) + IS_NULL(phone)</Code> — how many contact details
            are still missing, as a number you can sort by.
          </li>
          <li>
            <Code>IF(IS_NULL(sent_on), "Draft", "Sent")</Code> — a status worked out
            from whether a date has been filled in.
          </li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          Empty is not the same as zero — a <em>Quantity</em> of 0 is a value
          someone typed, so <Code>IS_NULL(qty)</Code> is <Code>0</Code> for it and{" "}
          <Code>DEFAULT(qty, 99)</Code> leaves it alone. The two differ over a cell
          that has been emptied to nothing: <Code>DEFAULT</Code> counts that as a
          gap and fills it, <Code>IS_NULL</Code> counts it as an answer.
        </p>

        <h3 className="font-semibold pt-1">A few worked examples</h3>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>ROUND(qty * unit_price, 2)</Code> — a line total, to two decimal
            places.
          </li>
          <li>
            <Code>CONCAT(TRIM(first_name), " ", UPPER(LEFT(last_name, 1)), ".")</Code>{" "}
            — <em>Ada L.</em>
          </li>
          <li>
            <Code>IF(IS_NULL(notes), "—", "Has notes")</Code> — a tidy flag
            column.
          </li>
          <li>
            <Code>CONCAT(DEFAULT(nickname, first_name), " ", last_name)</Code> —
            what to call someone, whether or not they gave a nickname.
          </li>
          <li>
            <Code>MAX(0, total - paid)</Code> — what’s still owed, never negative.
          </li>
        </ul>

        <h3 className="font-semibold pt-1">Every function at a glance</h3>
        <p>
          The whole list, for looking one up. Anything with a <Code>?</Code> after
          it can be left out. Function names are always in capitals; the column
          names in the examples are made up, so swap in your own.
        </p>
        <FunctionTable />
        <p style={{ color: "var(--text-muted)" }}>
          Metrics — the summary cards above a list — understand all of these too,
          and add their own for totalling a whole column. See{" "}
          <a href="#metrics" style={{ color: "var(--brand)" }}>
            Metrics
          </a>
          .
        </p>

        <Callout tone="warn" title="What a calculated column can’t do">
          <ul className="flex flex-col gap-1 m-0">
            <li>
              The formula is the only thing that writes it, so you can’t type into
              it, give it a <Ui>Default value</Ui>, or mark it <Ui>Required</Ui> —
              there’s no cell for anyone to fill in or leave blank.{" "}
              <Ui>Unique</Ui> is the exception, and it’s below.
            </li>
            <li>
              It can read another calculated column, but the two can’t end up
              depending on each other — a loop is refused when you save it.
            </li>
            <li>
              A value for it in an imported file is ignored: the formula works it
              out from the row itself, so a file this app writes always imports
              cleanly back in.
            </li>
            <li>
              It has to stay a sensible size. A formula can be up to 1,000
              characters long and around 32 layers deep — brackets inside
              brackets, or functions wrapped around functions. Past either, it’s
              refused when you save, with a message saying which. You are
              unlikely to meet these by hand: the worked examples above are two or
              three layers deep.
            </li>
          </ul>
        </Callout>
        <Callout tone="tip" title="A blank cell means “not yet”">
          If any column the formula names is empty on that record, the result is
          blank (—) rather than zero — an unfinished row should look unfinished.
          Dividing by zero comes out blank the same way, so one odd record never
          spoils the rest of the list. So does a result that would run to tens of
          thousands of characters: a formula fills a cell in a table, and{" "}
          <Code>REPLACE</Code> in particular can grow text faster than you’d
          expect.
        </Callout>
        <Callout tone="warn" title="Renaming breaks a formula quietly">
          Formulas find columns by their short name. If you rename a column a
          formula uses, the formula stops finding it and those cells simply go
          blank — no error, and the saved copy that sorting uses goes blank with it.
          After renaming, open the calculated column and update
          the formula to match.
        </Callout>
        <Callout tone="tip" title="Unique: a key made of several columns">
          Tick <Ui>Unique</Ui> on a calculated column and no two records may work
          out to the same answer — <Code>CONCAT(course, "-", section)</Code> is how
          you say “this pair may only appear once”, which no single column can. The
          record you’re saving is refused, even though you were editing the columns
          the formula reads rather than the answer itself, so the message names the
          calculated column and the value it worked out. Two things it won’t do:
          existing duplicates are left alone until one of them is next saved, and
          changing the formula so that records collide doesn’t go back and complain
          about them either.
        </Callout>
      </Section>

      <Section id="qr" title="QR codes: scan records in, print badges out">
        <p>
          If your information already lives in a QR code, you can add a record by
          scanning it instead of typing.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Click the little arrow beside <Ui>Add record</Ui> to open its menu.
          </li>
          <li>
            Choose <Ui>Fill using QR code</Ui> to put the scanned values into the form
            so you can check them first, or <Ui>Submit using QR code</Ui> to save
            records as they are scanned.
          </li>
          <li>
            Point your camera at the code. No camera? Click <Ui>Scan an image</Ui> and
            pick a saved picture of the code instead.
          </li>
        </ol>
        <Figure name="qr-menu" />
        <ul className="flex flex-col gap-1">
          <li>
            The code has to hold the record’s fields (by their column names) and
            values. A code that holds something else — a website, Wi-Fi details — is
            politely refused.
          </li>
          <li>
            <Ui>Fill using QR code</Ui> reads one code and stops at the form.
          </li>
        </ul>

        <h3 className="font-semibold pt-1">
          Scanning a queue — attendance and the like
        </h3>
        <p>
          If everyone has a badge with their own QR code, <Ui>Submit using QR code</Ui>
          is built to work through them one after another.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            First you’re asked for the values that are the same for everybody — the
            event, the session, the date. Fill in those and leave the rest blank; each
            badge fills the rest. Then click <Ui>Start scanning</Ui>.
          </li>
          <li>
            Scan each person’s code in turn. A record is saved as each one is read, and
            the scanner stays open — you don’t reopen it between people. A running
            count and the last few scans are shown under the camera.
          </li>
          <li>
            Click <Ui>Done</Ui> when the queue is finished.
          </li>
        </ol>
        <ul className="flex flex-col gap-1">
          <li>
            Holding one badge in front of the camera saves <em>one</em> record, not a
            stream of them — the same code is ignored for a few seconds after it’s
            read. Scanning the same person again later does save a second record.
          </li>
          <li>
            If a badge carries a field you also set at the start, the badge wins.
          </li>
          <li>
            A code the app rejects doesn’t stop the queue: the reason is shown under
            the camera and in the list of recent scans, and the next person can scan
            straight away. Rescanning the same code won’t help — it’ll be refused for
            the same reason — so add that one record by hand afterwards.
          </li>
          <li>
            <Ui>Change values</Ui> goes back to the shared values without losing your
            count — useful when the session changes but the queue doesn’t.
          </li>
        </ul>

<h3 className="font-semibold pt-1">Making the badges in the first place</h3>
        <p>
          The codes above have to come from somewhere, and that somewhere can be this
          app. Any record can be turned into a QR code, and a whole batch can be printed
          as a sheet — which is how you get a badge per person without authoring
          anything by hand.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            For one record, open its <Ui>QR code</Ui> action (the little square-ish icon
            on the row, or under the <Ui>⋮</Ui> menu if the row is narrow).
          </li>
          <li>
            For many, tick the records you want and click <Ui>QR codes</Ui> in the bar
            that appears at the bottom of the screen.
          </li>
          <li>
            Choose which fields each code should carry, pick a printed size, and click
            <Ui>Print</Ui>. A single code can also be saved as a picture (<Ui>PNG</Ui> or
            <Ui>SVG</Ui>) if you want to put it somewhere else.
          </li>
        </ol>
        <ul className="flex flex-col gap-1">
          <li>
            <strong>Fewer fields make a better code.</strong> Every field adds to what the
            code has to hold, and a code holding more is printed with finer detail —
            which is harder for a phone to read off paper. If you are warned that the
            codes are dense, take a field off or print them larger. A name is usually
            enough.
          </li>
          <li>
            <strong>Record id</strong> is on by default. It is what lets a code say{" "}
            <em>which</em> record it belongs to, which matters if something outside this
            app will read the codes. Scanning one back in here ignores it — a scan
            makes a new record rather than finding the old one.
          </li>
          <li>
            The fields you choose are remembered <strong>for the list</strong>, so every
            batch of badges you print comes out the same. Saving that choice needs
            permission to configure the list; without it you can still change the fields
            for the print in front of you.
          </li>
          <li>
            A code made here can be scanned straight back in with <Ui>Fill using QR
            code</Ui> above — which is the quickest way to check one is right.
          </li>
          <li>
            Files are the one thing a code cannot carry. An attachment column puts the
            file’s <em>name</em> on the code, and scanning that back will not bring the
            file with it.
          </li>
        </ul>
            </Section>

      <Section id="find" title="Find records: search, sort, group, filter">
        <p>
          As a list grows, four tools help you find what you need. They live in the
          bar above your records.
        </p>
        <Figure name="toolbar" />
        <ul className="flex flex-col gap-2">
          <li>
            <strong>Search</strong> — type in <Ui>Search records…</Ui> to narrow to
            rows that match. It searches the whole list, not just the page you’re on.
          </li>
          <li>
            <strong>Sort</strong> — click <Ui>Sort</Ui>, pick a column, and choose{" "}
            <Ui>Ascending</Ui> (A→Z) or <Ui>Descending</Ui> (Z→A). The button then
            shows what you sorted by. (On a wide screen you can also click a column
            heading.)
          </li>
          <li>
            <strong>Group</strong> — click <Ui>Group</Ui> and choose a column to
            bunch rows with the same value together (all <em>Customers</em>{" "}
            together, say).
          </li>
          <li>
            <strong>Filter</strong> — click <Ui>Filter</Ui> to show only rows that
            meet conditions you set.
          </li>
        </ul>
        <p>To build a filter:</p>
        <ol className="flex flex-col gap-2">
          <li>Click <Ui>Filter</Ui>, then <Ui>Add condition</Ui>.</li>
          <li>
            Pick a <Ui>Column</Ui>, choose how to compare it under <Ui>Op</Ui> (like{" "}
            <Ui>is</Ui>, <Ui>contains</Ui>, <Ui>is empty</Ui>, or <Ui>≥</Ui>), and
            enter a <Ui>Value</Ui>.
          </li>
          <li>
            Add more conditions if you like. With more than one, choose{" "}
            <Ui>Match all</Ui> (a row must meet every rule) or <Ui>Match any</Ui> (at
            least one rule).
          </li>
          <li>Click <Ui>Apply</Ui>.</li>
        </ol>
        <Figure name="filter" />
        <p style={{ color: "var(--text-muted)" }}>
          The value box adapts to the column: numbers get a number box, dates get a
          calendar, and choice/people/linked columns give you a picker. Comparisons
          like <Ui>is empty</Ui> need no value at all.
        </p>
        <Callout tone="tip" title="“Today” and “Now” move with the calendar">
          On a date, time or date &amp; time column the value box offers{" "}
          <Ui>Today</Ui> or <Ui>Now</Ui> as well as a date you pick. Choose one and
          the filter is worked out afresh every time you look — so{" "}
          <Ui>Due</Ui> <Ui>≥</Ui> <Ui>Today</Ui> saved into a view still means{" "}
          <em>today</em> next month, where a date you typed in would still mean the
          day you typed it. On a date &amp; time column, <Ui>Today</Ui> means from
          midnight and <Ui>Now</Ui> means from this minute.
        </Callout>
        <Callout tone="warn" title="Nothing sticks until you save it">
          Searching, sorting, grouping, and choosing columns change only how{" "}
          <em>you</em> are looking right now. To keep an arrangement, save it into a
          view (next section).
        </Callout>
      </Section>

      <Section id="views" title="Views: saved ways of looking">
        <p>
          A <strong>view</strong> remembers a filter, a grouping, a sort, and which
          columns show — so you can flip between, say, “All contacts” and “Customers
          only, sorted by name” with one click.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            To switch views, use the <Ui>View</Ui> dropdown at the top of the list.
          </li>
          <li>
            To make one, click the <Ui>+</Ui> beside that dropdown, type a{" "}
            <Ui>View name</Ui>, and click <Ui>Create view</Ui>. It starts from
            whatever you are looking at right now.
          </li>
          <li>
            Set up the sort, filter, grouping, and columns you want. A{" "}
            <Ui>Save view</Ui> button appears on the bar as soon as anything
            differs from what the view has stored — click it to keep the new
            arrangement. If you don’t see it, there is nothing to save.
          </li>
        </ol>
        <Figure name="views" />
        <ul className="flex flex-col gap-1">
          <li>
            Use <Ui>Columns</Ui> to tick which columns appear. To reorder them,
            drag a column by the grip on its left, or step it one place at a time
            with <Ui>Move up</Ui> / <Ui>Move down</Ui> — the buttons also work from
            the keyboard.
          </li>
          <li>
            You can rename or delete a view from its settings. Deleting a view only
            removes that saved arrangement — your records are untouched — and you can’t
            delete the last remaining view.
          </li>
        </ul>
      </Section>

      <Section id="forms" title="Forms: shape the entry screen">
        <p>
          A <strong>form</strong> controls the screen people see when adding or
          editing a record — which fields appear, in what order, and what they’re
          called. Every list has a default form, and you can tune it under{" "}
          <Ui>Settings</Ui> → <Ui>Forms</Ui>.
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            Pick the form to edit from the dropdown (the main one is marked{" "}
            <Ui>(default)</Ui>).
          </li>
          <li>
            Tick <Ui>Shown</Ui> to include a field; tick <Ui>Editable</Ui> to let
            people type into it (a hidden field can’t be edited).
          </li>
          <li>
            Reorder fields by dragging the grip on the left, or with{" "}
            <Ui>Move up</Ui> / <Ui>Move down</Ui>, and give a field a friendlier{" "}
            <Ui>Label</Ui> just for this form.
          </li>
          <li>Click <Ui>Save</Ui> to apply. New fields start out shown and editable.</li>
        </ul>
        <Figure name="forms" />
        <p style={{ color: "var(--text-muted)" }}>
          The four automatic columns — <Ui>Created at</Ui>, <Ui>Updated at</Ui>,{" "}
          <Ui>Created by</Ui>, <Ui>Updated by</Ui> — are the exception: they start
          hidden and not editable, since the app maintains them. Tick <Ui>Shown</Ui>{" "}
          if you want them visible while someone fills the form in.
        </p>
      </Section>

      <Section id="metrics" title="Metrics: totals, averages and counts">
        <p>
          A <strong>metric</strong> answers a question about the whole list rather
          than about one record: <em>how many hours altogether?</em>,{" "}
          <em>what does a job cost on average?</em>, <em>how many people are on
          this?</em> Each one shows as a small card above the records.
        </p>
        <p>
          Add one under <Ui>Settings</Ui> → <Ui>Metrics</Ui> →{" "}
          <Ui>Add metric</Ui>. Give it a name, then write a formula in the{" "}
          <Ui>Formula</Ui> box — the dialog lists the columns you can use.
        </p>
        <p>
          The cards stay <strong>out of the way until you ask for them</strong>:
          click <Ui>Metrics</Ui> on the toolbar above the records to show or hide
          them. The number on that button is how many the list has, so you can tell
          a hidden strip from a list with none. They start hidden every time you
          open a list — working out a total means reading every record, so it is
          not something the app does over your shoulder.
        </p>
        <Figure name="metrics" />

        <h3 className="font-semibold pt-1">Writing the formula</h3>
        <p>
          Wrap the column you want in one of these, in <strong>CAPITALS</strong>:
        </p>
        <ul className="flex flex-col gap-1">
          <li><Code>SUM(hours)</Code> — everything added up.</li>
          <li><Code>AVG(cost)</Code> — the average.</li>
          <li>
            <Code>COUNT()</Code> — how many records there are. Name a column —{" "}
            <Code>COUNT(owner)</Code> — to count only the ones that have it filled
            in.
          </li>
          <li><Code>MIN(price)</Code> / <Code>MAX(price)</Code> — the smallest and largest.</li>
          <li><Code>MEDIAN(price)</Code> — the middle value.</li>
          <li><Code>UNIQUE(owner)</Code> — how many different values appear.</li>
          <li><Code>EMPTY(owner)</Code> — how many records are missing it.</li>
        </ul>
        <p>
          You can do sums <em>inside</em> a total, which is usually what you want:{" "}
          <Code>SUM(qty * price)</Code> multiplies each record first and adds the
          results up — quite different from multiplying two totals together. And you
          can do sums <em>with</em> totals: <Code>SUM(revenue) - SUM(cost)</Code>, or{" "}
          <Code>ROUND(SUM(hours) / COUNT(), 2)</Code>.
        </p>
        <p style={{ color: "var(--text-muted)" }}>
          A column on its own isn’t enough — <Code>hours</Code> is a different number
          on every record, so the box will ask you to say what to do with them all:{" "}
          <Code>SUM(hours)</Code>. Number, <strong>Rating</strong>,{" "}
          <strong>Duration</strong>, text and calculated columns can be measured — so{" "}
          <Code>AVG(score)</Code> over a rating and <Code>SUM(length)</Code> over a
          duration both work. Dates, choices and linked columns can’t yet, though a
          calculated column that reads one gets you there.
        </p>

        <h3 className="font-semibold pt-1">What the number counts</h3>
        <ul className="flex flex-col gap-1">
          <li>
            <strong>Whatever is on screen.</strong> Switch to a view that filters the
            list, or type in the search box, and the cards follow — so they always
            describe the records you are actually looking at.
          </li>
          <li>
            <strong>Only records you’re allowed to see.</strong> If a list is shared
            with you but some records are not, your totals cover your records. Two
            people can see different numbers on the same card, and both are right.
          </li>
          <li>
            <strong>Empty cells are skipped, not counted as zero.</strong> An average
            over three records where one is blank is the average of two — a missing
            value shouldn’t drag an average down.
          </li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          An average of <em>nothing</em> is blank rather than 0, since there is no
          such number to show. If you’d rather see a figure,{" "}
          <Code>DEFAULT</Code> works here as it does in a formula:{" "}
          <Code>DEFAULT(AVG(cost), 0)</Code>.
        </p>
        <Callout tone="tip" title="If a card shows a warning">
          A metric finds its columns by their short name. Rename a column it uses and
          the card says which one it can no longer find, rather than quietly showing
          zero. Edit the metric and fix the name.
        </Callout>
      </Section>

      <Section id="sharing" title="Sharing and permissions">
        <p>
          Nothing is shared until you say so. You can share a whole workspace, a
          single list, an individual record, or a form — and control exactly what each
          person or group may do.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Open sharing for the item — the share icon on a workspace or list card,
            or <Ui>Settings</Ui> → <Ui>Permissions</Ui> once the list is open.
          </li>
          <li>
            Under <Ui>Add people or groups</Ui>, type a name to find the person or
            group.
          </li>
          <li>
            Give them access — either a one-click role (<Ui>Lookup only</Ui>,{" "}
            <Ui>Viewer</Ui>, <Ui>Editor</Ui>, or <Ui>Manager</Ui>) or tick the exact
            actions you want.
          </li>
          <li>
            Optionally set an <Ui>Expires (optional)</Ui> date so access ends by
            itself. Then click <Ui>Add</Ui>.
          </li>
        </ol>
        <Figure name="sharing" />
        <p>The individual actions mean:</p>
        <ul className="flex flex-col gap-1">
          <li><strong>View</strong> — see the item and its records.</li>
          <li><strong>Create</strong> — add records.</li>
          <li><strong>Edit</strong> — change records.</li>
          <li><strong>Delete</strong> — remove records or the item.</li>
          <li><strong>Configure</strong> — change settings, columns, forms, and views.</li>
          <li><strong>Share</strong> — manage who has access.</li>
          <li><strong>Manage</strong> — full control (includes all of the above).</li>
          <li>
            <strong>Lookup</strong> — use the list as a lookup target: its values can
            be picked and shown in someone else’s lookup column, but the list itself
            stays hidden.
          </li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          The roles are just shortcuts: <em>Lookup only</em> = Lookup;{" "}
          <em>Viewer</em> = View; <em>Editor</em> = View, Create, Edit, Delete;{" "}
          <em>Manager</em> = full control. Anyone who can View can also look up, so
          you only need <em>Lookup</em> on its own.
        </p>
        <Callout tone="tip" title="Sharing a list you don’t want to hand over">
          <p>
            <Ui>Lookup</Ui> is for the lists that exist to be referenced —{" "}
            <em>Schools</em>, <em>Departments</em>, <em>Countries</em>. Share one that
            way and other people can point a lookup column at it and see its labels in
            their own records, but it never appears in their{" "}
            <Ui>Shared with me</Ui> and they cannot open it, browse its records, or see
            what columns it has.
          </p>
          <p>
            It still shows them the values of the labelling column for every row they
            can reach, so use it for reference data rather than for a list with
            something to hide in it.
          </p>
        </Callout>
        <Callout tone="tip" title="Good to know">
          The owner of an item always keeps full access. A row shows a friendly
          reminder like <Ui>Expires today</Ui> when access is ending, and{" "}
          <Ui>Expired</Ui> once it has. For a list you can turn on{" "}
          <Ui>Unique permissions</Ui> to ignore access handed down from its workspace
          and use only the people you add here. Sharing with a group grants everyone
          in it — a quick way to give a whole team access at once.
        </Callout>
        <Callout tone="tip" title="Sharing with everybody">
          One group, <Ui>Authenticated Users</Ui> (<code>authenticated_users</code> in
          a file), is built in and always means “everyone with an account here”,
          including people who join later. Share with it when something is meant for
          the whole organisation. On the <Ui>People</Ui> page it looks like a group
          with no members: that’s expected, since membership is automatic rather than
          a list someone maintains. For the same reason the app won’t let anyone
          rename, edit, or delete it.
        </Callout>
      </Section>

      <Section id="transfer" title="Moving and giving things away">
        <p>
          A list doesn’t have to stay where you made it. Open a workspace to see its
          lists; each one has a <Ui>Move or transfer</Ui> button (the paper-plane
          icon), alongside Share, Rename, and Delete.
        </p>
        <Figure name="transfer" />
        <p>
          <strong>Move it to another workspace.</strong> Choose{" "}
          <Ui>Move to workspace</Ui>, pick one of your other workspaces, and click{" "}
          <Ui>Move</Ui>. Everything travels with it — columns, records, views, and
          forms — and everyone you’ve already shared it with keeps their access. You can
          only move a list into a workspace you’re allowed to set up.
        </p>
        <p>
          <strong>Give it to another person.</strong> Choose{" "}
          <Ui>Transfer to user</Ui>, pick the person, and click <Ui>Transfer</Ui>. The
          list becomes theirs:
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            It lands in that person’s <Ui>Incoming</Ui> workspace — a folder everyone
            has for lists sent to them.
          </li>
          <li>
            They become the owner, so <strong>you lose access</strong> unless they later
            share it back. People you’d already shared it with keep theirs.
          </li>
          <li>Only a list’s owner can give it away.</li>
          <li>
            The person receiving it can keep it — moving it out of{" "}
            <Ui>Incoming</Ui> into a workspace of their own — or turn it down by deleting
            it.
          </li>
        </ul>
        <Callout tone="tip" title="Your Incoming folder">
          Once someone transfers you a list, an <Ui>Incoming</Ui> workspace appears among
          your workspaces. You can’t rename or delete it — it’s just a landing spot — but
          the lists inside are yours to move elsewhere or remove.
        </Callout>
        <p>
          <strong>Give away a whole workspace.</strong> On the <Ui>Workspaces</Ui> page,
          each workspace you own has its own <Ui>Transfer workspace</Ui> button behind the
          same paper-plane icon. Every list inside goes with it in one step, and nothing
          changes name or address on the way — no <Ui>Incoming</Ui> folder is involved,
          because the workspace itself is the thing changing hands. As with a list, only
          the owner can give one away, everyone you’d shared it with keeps their access,
          and you lose yours. If the person already has a workspace by that name, the one
          arriving is renamed rather than refused.
        </p>
        <Callout tone="warn" title="Transferring is one-way">
          After you transfer a list — or a workspace, and all the lists in it — it belongs
          to the other person; getting it back means asking them to transfer or share it to
          you. If you only want someone to help with it,{" "}
          <a href="#sharing" style={{ color: "var(--brand)" }}>share it</a> with them
          instead of transferring it.
        </Callout>
      </Section>

      <Section id="duplicate" title="Duplicating a list">
        <p>
          Need a second list just like one you already have — a fresh month’s tracker,
          or a template to build on? Open a workspace and click the{" "}
          <Ui>Duplicate</Ui> button (the copy icon) on any list.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Decide whether to bring the data along. By default the copy takes only the{" "}
            <em>structure</em> — the columns, views, and forms — and starts empty. Tick{" "}
            <Ui>Also copy the records</Ui> if you want every row copied too.
          </li>
          <li>
            Click <Ui>Duplicate</Ui>. The copy appears in the same workspace with a name
            like <em>contacts_copy</em>, which you can rename afterward.
          </li>
        </ol>
        <Figure name="duplicate" />
        <ul className="flex flex-col gap-1">
          <li>
            The copy is entirely your own and starts <strong>private</strong> — none of
            the original’s sharing comes across, so you decide who sees it.
          </li>
          <li>
            It’s made where the original lives. To put it in another workspace, duplicate
            it here and then <a href="#transfer" style={{ color: "var(--brand)" }}>move
            it</a>.
          </li>
        </ul>
        <Callout tone="tip" title="Structure now, data later">
          Leaving the records out is the quick way to turn a finished list into a
          reusable template — duplicate it empty, then fill the copy fresh. Copy the
          records when you truly want a running duplicate of everything as it stands.
        </Callout>
      </Section>

      <Section id="people" title="People, groups, and your profile">
        <p>
          The <Ui>People</Ui> page lists everyone with an account and every group.
          Anyone can look; only an <strong>administrator</strong> (a “superuser”) can
          add or change accounts.
        </p>
        <Figure name="people" />
        <ul className="flex flex-col gap-1">
          <li>
            Administrators can <Ui>Add user</Ui> (username, email, password), reset
            passwords, mark accounts active or inactive, and grant administrator
            rights.
          </li>
          <li>
            Setting up a whole class or team? <Ui>Import users</Ui> takes a
            spreadsheet file instead — one row per person, so you fill it in once
            rather than working the form twenty times. Click{" "}
            <Ui>Download template</Ui> inside it to get a file with the right
            headings and one filled-in example row to copy. You can give each person
            a profile in the same file: fill in the name columns and they get one,
            leave them empty and they don’t. The <Ui>groups</Ui> column wants each
            group’s <Ui>API name</Ui> — the short one shown under the group on the{" "}
            <Ui>People</Ui> page — separated by semicolons. Get one wrong and the app
            tells you which name to use instead.
          </li>
          <li>
            They can also create groups (<Ui>New group</Ui>) and choose their members —
            handy for sharing with a whole team in one go. A group has two names, the
            same as a list does: the <Ui>Name</Ui> you read on screen, and an{" "}
            <Ui>API name</Ui> underneath it — a short one like <code>year_9</code>{" "}
            that files use. Renaming the first is free; the second is what an import
            file and an exported spreadsheet refer to, so changing it can leave older
            files pointing at nothing.
          </li>
          <li>
            Everyone has a <Link to="/profile" style={{ color: "var(--brand)" }}>
              Profile
            </Link>{" "}
            page for their own name and details, reached from your account at the
            bottom of the strip on the left. Filling it in is optional and doesn’t
            affect signing in. The name you put there is what your initials in that
            circle come from.
          </li>
        </ul>
        <Callout tone="warn" title="Passwords from an import are shown once">
          Leave the <Ui>password</Ui> column empty in the file and the app makes a
          strong password for that person. Those appear only on the panel you see
          straight after the import — click <Ui>Download credentials</Ui> to save
          them before you close it. They are not kept anywhere, so there is no way to
          look one up later; if one goes missing, an administrator sets a new
          password from <Ui>Edit user</Ui>.
        </Callout>
        <Callout tone="tip" title="Changing your password">
          When you change your own password you’ll be asked for your current one too.
          People are always shown to others by their username, never their personal
          name, to keep private details private.
        </Callout>
      </Section>

      <Section id="import-export" title="Import and export">
        <p>
          You don’t have to type everything. Open a list, click the <Ui>⋯</Ui>{" "}
          button beside <Ui>Settings</Ui>, and choose <Ui>Import / Export</Ui>. It
          has a tab for each direction.
        </p>
        <p>
          <strong>To take data out:</strong>
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Choose a <Ui>Format</Ui> — <Ui>CSV</Ui> (opens in any spreadsheet) or{" "}
            <Ui>JSON</Ui>.
          </li>
          <li>
            Tick the <Ui>Columns</Ui> you want included. You can also set a{" "}
            <Ui>Limit</Ui>, a <Ui>Sort</Ui>, and even <Ui>Filters (optional)</Ui> so
            only some rows come out.
          </li>
          <li>Click <Ui>Export records</Ui> to download the file.</li>
        </ol>
        <Figure name="import-export" />
        <p>
          Already looking at exactly the rows you want? Click{" "}
          <Ui>Use current filters and sort</Ui> under <Ui>Which records</Ui> and the
          filter and sort you have on the grid are copied straight into those fields,
          so the file matches the screen. They stay ordinary fields afterwards — add
          a condition, or change the direction, before you export. The one thing it
          can’t bring across is the search box: a search isn’t a filter, so the rows
          it is hiding will be in the file unless you clear it or write the
          equivalent condition yourself.
        </p>
        <p>
          <strong>To bring data in:</strong> under <Ui>Import</Ui>, click{" "}
          <Ui>Choose file</Ui> and pick a <em>.csv</em> or <em>.json</em> file. After
          it uploads you’ll see how many records were added and, if any rows were
          skipped, the reason for each.
        </p>
        <p>
          When rows are skipped you also get a <Ui>Download skipped rows</Ui> button.
          It hands you back just those rows, exactly as you uploaded them, with the
          reason in an extra <Ui>_error</Ui> column — so instead of hunting for line
          47 of the original, you fix the cells it complains about and upload{" "}
          <em>that</em> file. The <Ui>_row</Ui> and <Ui>_error</Ui> columns are
          ignored when you do, so there’s no need to delete them first.
        </p>
        <p>
          Starting from nothing? Pick a <Ui>Template format</Ui> and click{" "}
          <Ui>Download template</Ui> on that same tab: you get an empty file with one
          heading per column, ready to fill in and upload straight back. Calculated
          columns aren’t in the template — they work themselves out from your other
          columns, so there is nothing to fill in. They <em>are</em> on the{" "}
          <Ui>Columns</Ui> and <Ui>Sort by</Ui> lists, because an export is a file
          you read: the worked-out value goes into it, and is ignored again if you
          upload the same file back.
        </p>
        <Callout tone="warn" title="Match the headings">
          Your file’s column headings must match your list’s underlying column names
          (the short name shown in brackets next to each column), not necessarily the
          pretty display names. Headings the list doesn’t recognise are simply
          ignored, and a bad row is skipped while the good rows still import — nothing
          all-or-nothing.
        </Callout>
        <p>
          This is for the <em>records inside a list</em>. Setting up a lot of{" "}
          <em>people</em> is a separate thing in a separate place — see{" "}
          <a href="#people" style={{ color: "var(--brand)" }}>
            People, groups, and your profile
          </a>
          . One difference worth knowing if you do both: a list ignores a heading it
          doesn’t recognise, because every list has its own columns. The people file
          refuses one instead, because there its headings are a fixed set, and a
          misspelt <Ui>first_name</Ui> would otherwise quietly leave every name blank.
        </p>
      </Section>

      <Section id="archive" title="Archiving records you’re done with">
        <p>
          Lists grow, and most of what fills them stops being interesting long before
          anyone wants it gone: last year’s orders, closed tickets, people who left.
          Deleting them is wrong — you may well need them again, and the bin would
          destroy them in a month. <strong>Archiving</strong> is the answer to that:
          it puts a record away without throwing it away.
        </p>
        <p>
          An archived record keeps everything it had, and <strong>stays for good</strong>
          . There is no clock on it: it waits until somebody brings it back or
          deliberately deletes it.
        </p>
        <p>What changes is where it shows up. An archived record drops out of:</p>
        <ul className="flex flex-col gap-1">
          <li>the table, and the record count above it;</li>
          <li>searches, filters and sorting;</li>
          <li>your metrics — totals and averages stop counting it.</li>
        </ul>
        <p>
          That last one is the point as much as the tidiness is. A list the app no
          longer has to read through on every search and every total is a list that
          stays quick, however much history has piled up behind it.
        </p>
        <p>
          <strong>Archiving something.</strong> Open the <Ui>⋯</Ui> menu on a record’s
          row and choose to archive it, or tick several rows and click{" "}
          <Ui>Archive selected</Ui>.
        </p>
        <p>
          <strong>Getting it back.</strong> Go to <Ui>⋯</Ui> →{" "}
          <Ui>Archived records</Ui>, which lists everything put aside, and bring back
          what you need. It returns exactly as it was.
        </p>
        <Callout tone="tip" title="Links to an archived record still read properly">
          If another list points at a record you archive, that link doesn’t break or
          empty itself — it goes on showing the name as before. Archiving changes
          which lists a record appears in, not whether it exists.
        </Callout>
        <Callout tone="warn" title="Archiving is not deleting, and the bin is not archiving">
          They are two separate things and either can be undone, so the only real
          mistake is reaching for the wrong one. <strong>Archive</strong> what you are
          finished with — it is kept indefinitely. <strong>Delete</strong> what should
          never have been there — and that one is on a clock, described next.
        </Callout>
      </Section>

      <Section id="bin" title="Recovering things you deleted">
        <p>
          Deleting doesn’t destroy anything straight away. Everything you can delete
          goes to a <strong>bin</strong> you can fetch it back from, and each bin sits
          beside the thing it holds.
        </p>
        <ul className="flex flex-col gap-2">
          <li>
            <strong>A record.</strong> Open the list, click the <Ui>⋯</Ui> button
            beside <Ui>Settings</Ui>, and choose <Ui>Deleted records</Ui>.
          </li>
          <li>
            <strong>A list.</strong> Open the workspace it was in and click{" "}
            <Ui>Deleted</Ui>.
          </li>
          <li>
            <strong>A workspace.</strong> Go to <Ui>Workspaces</Ui> and click{" "}
            <Ui>Deleted</Ui>.
          </li>
          <li>
            <strong>A column, view, form, or metric.</strong> Open the list, click{" "}
            <Ui>Settings</Ui>, go to that tab, and click <Ui>Deleted</Ui> — each of
            the four has its own bin, on its own tab.
          </li>
          <li>
            <strong>An account or group.</strong> Go to <Ui>People</Ui> and click{" "}
            <Ui>Deleted</Ui> in the <Ui>Users</Ui> or <Ui>Groups</Ui> section. This
            one is for administrators only.
          </li>
        </ul>
        <p>
          Each shows what was removed and when. Click <Ui>Restore</Ui> beside an
          entry to bring it back, with everything it contained. Beside that sits a
          red bin icon, <Ui>Delete permanently</Ui>, for when you want something
          gone now rather than at the end of its window — it asks you to confirm,
          and there is no undo.
        </p>
        <Figure name="bin" />
        <Callout tone="warn" title="There’s a time limit — about 30 days">
          Deleting is not the same as putting something aside: the bin runs on a
          clock. By default a deleted thing is kept for <strong>30 days</strong> and
          then destroyed for good, with no way back. Whoever runs this app can set a
          different length for your organisation, so check with them if it matters —
          but assume a month unless you have been told otherwise. Each entry in the
          bin shows when it was deleted, which is how you tell what is nearly out of
          time.
        </Callout>
        <Callout tone="tip" title="Done with a record rather than sorry you made it?">
          The bin is for mistakes. For records you are simply finished with — last
          year’s orders, closed tickets — use <strong>archiving</strong> instead,
          which has no clock on it at all. See{" "}
          <a href="#archive" style={{ color: "var(--brand)" }}>
            Archiving records you’re done with
          </a>
          .
        </Callout>
        <Callout tone="tip" title="A list deleted with its workspace">
          Restoring a workspace brings its lists back with it. That’s why a list that
          went down with its workspace isn’t in the <Ui>Deleted lists</Ui> bin on its
          own — restore the workspace and you’ll find it inside.
        </Callout>
        <Callout tone="warn" title="Links to a deleted list don’t come back">
          If other lists had{" "}
          <a href="#nested-lookups" style={{ color: "var(--brand)" }}>
            link columns
          </a>{" "}
          pointing at a list you delete, those cells are emptied — they’d point at
          nothing. Restoring the list brings back its own records, but not the links
          other lists had to it; you’d pick those again.
        </Callout>
        <Callout tone="warn" title="A deleted column is hidden, not erased">
          Deleting a column hides what it held; every record still carries those
          values, which is what lets <Ui>Restore</Ui> bring them back in full.{" "}
          <Ui>Delete permanently</Ui> is the one that actually erases them, from
          every record at once. If you deleted a column to get rid of what was in
          it, that second step is the one that does it.
        </Callout>
      </Section>

      <Section id="appearance" title="Appearance and themes">
        <p>
          The two small icons beside your name, at the bottom of the strip on the
          left, change how the app looks. One flips between <strong>light</strong>{" "}
          and <strong>dark</strong>; the other picks a colour theme (there are
          several, from a clean modern look to a bold high-contrast one). Pick
          whatever is easiest on your eyes. On a narrow window they sit at the top
          right instead.
        </p>
        <Figure name="appearance" />
        <p>
          Your choice is saved to your account, not to the browser, so signing in on
          another computer or phone brings it with you. Changing it anywhere changes
          it everywhere the next time you open the app.
        </p>
        <Callout tone="tip" title="Your time zone works the other way round">
          How the app looks follows your account; what it thinks the date is follows
          the <em>device you are on</em>. Each time you open the app it quietly tells
          the server which time zone this browser is in, which is what makes a date
          box default to your today rather than to somewhere else’s. You never set
          this and there is nothing to configure — but it does explain a date landing
          a day out if you are travelling, or on a work laptop whose clock is set to
          head office. Fix the device’s time zone and the app follows.
        </Callout>
      </Section>

      <Section id="tips" title="Tips and troubleshooting">
        <ul className="flex flex-col gap-2">
          <li>
            <strong>A button is missing.</strong> The app hides actions you’re not
            allowed to do. If you can’t rename, share, or delete something, you likely
            have view-only access — ask its owner.
          </li>
          <li>
            <strong>“Add a column first.”</strong> A list needs at least one column
            before you can add records, import, or use the bin. Set one up under{" "}
            <Ui>Settings</Ui> → <Ui>Columns</Ui>.
          </li>
          <li>
            <strong>My filter/sort disappeared.</strong> Those don’t save on their own.
            Use <Ui>Save view</Ui> to keep an arrangement.
          </li>
          <li>
            <strong>I can’t change a column’s type.</strong> That’s on purpose — add a
            new column of the type you need instead.
          </li>
          <li>
            <strong>A field is switched off and says “Select … first.”</strong>{" "}
            That column is{" "}
            <a href="#nested-lookups" style={{ color: "var(--brand)" }}>
              nested under another
            </a>
            . Fill in the one it names and this field wakes up, offering only the
            rows that go with your choice.
          </li>
          <li>
            <strong>Sorting is greyed out.</strong> Grouping fixes the order, so
            sorting turns off while records are grouped. Clear the grouping to sort
            again.
          </li>
          <li>
            <strong>A search found nothing on this page.</strong> Search covers the
            whole list and jumps you to the first page of matches — if it’s empty,
            there really are no matches.
          </li>
          <li>
            <strong>My calculated column went blank.</strong> Usually one of the
            columns its{" "}
            <a href="#formulas" style={{ color: "var(--brand)" }}>formula</a> reads
            is empty on that record — which shows as blank on purpose. If{" "}
            <em>every</em> record went blank at once, a column the formula names was
            probably renamed; update the formula to the new short name.
          </li>
          <li>
            <strong>I changed a formula and the sort order looks wrong.</strong> The
            values on screen are right straight away, but sorting, filtering and
            search read a saved copy that is rebuilt separately. Open{" "}
            <Ui>Settings</Ui> → <Ui>Columns</Ui> → <Ui>Refresh calculated</Ui> — the
            dialog offers this for you right after a formula edit — or leave it, and
            the nightly tidy-up catches it. The same is true of an{" "}
            <Code>AGE</Code> column on somebody’s birthday: the age shown is
            today’s, while sorting by it can be a day behind until that rebuild.
          </li>
        </ul>
        <p className="pt-2">
          Ready to start? Head to your{" "}
          <Link to="/workspaces" style={{ color: "var(--brand)", fontWeight: 600 }}>
            Workspaces
          </Link>{" "}
          and make your first list.
        </p>
      </Section>
    </div>
  );
}
