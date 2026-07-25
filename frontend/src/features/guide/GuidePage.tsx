import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Breadcrumbs } from "../../components/Breadcrumbs";
import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";

/**
 * A plain-language, start-to-finish guide to the app, written for people with no
 * technical background. It is a static document — no data is fetched — so it works
 * the moment someone signs in and never depends on having built anything yet.
 *
 * The section ids below are the anchor targets of the contents list at the top.
 * Keep SECTIONS and the <Section> ids in step: the list is generated from it.
 */

const SECTIONS: { id: string; title: string }[] = [
  { id: "what-it-is", title: "What this app is" },
  { id: "words", title: "Five words to know" },
  { id: "signing-in", title: "Signing in" },
  { id: "getting-around", title: "Getting around" },
  { id: "workspace", title: "Step 1 · Make a workspace" },
  { id: "list", title: "Step 2 · Make a list" },
  { id: "columns", title: "Step 3 · Add columns" },
  { id: "column-types", title: "The column types, explained" },
  { id: "records", title: "Step 4 · Add records" },
  { id: "nested-lookups", title: "Linked columns that narrow each other" },
  { id: "formulas", title: "Calculated columns: writing a formula" },
  { id: "qr", title: "Add records with a QR code" },
  { id: "find", title: "Find records: search, sort, group, filter" },
  { id: "views", title: "Views: saved ways of looking" },
  { id: "forms", title: "Forms: shape the entry screen" },
  { id: "sharing", title: "Sharing and permissions" },
  { id: "transfer", title: "Moving and giving a list away" },
  { id: "duplicate", title: "Duplicating a list" },
  { id: "people", title: "People, groups, and your profile" },
  { id: "import-export", title: "Import and export" },
  { id: "bin", title: "Recovering things you deleted" },
  { id: "appearance", title: "Appearance and themes" },
  { id: "tips", title: "Tips and troubleshooting" },
];

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

/** A titled section with a stable id so the contents list can jump to it. */
function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      className="flex flex-col gap-3"
      style={{ scrollMarginTop: "1rem" }}
    >
      <h2
        className="text-xl font-semibold pt-2"
        style={{ borderTop: "var(--border-width) solid var(--border)" }}
      >
        {title}
      </h2>
      {children}
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
  text: "A short line of writing — a name, a title, a reference number.",
  long_text: "Several lines of writing — notes, a description, an address.",
  number: "A number you can total or compare. You can set a smallest and largest allowed value, or require whole numbers only.",
  checkbox: "A simple yes/no, on/off tick — “Paid?”, “Active?”.",
  choice: "A fixed set of options you pick from, like Todo, Doing, Done — so everyone uses the same wording.",
  date: "A calendar day, with no time of day.",
  time: "A time of day, with no date.",
  datetime: "A day and a time together.",
  link: "A web address that becomes clickable. You can show friendly words in place of the address.",
  user: "A pointer to a person who has an account here. Can allow one person or several.",
  group: "A pointer to a group of people. Can allow one group or several.",
  lookup: "A link to a row in another list — one of yours, in any of your workspaces, or one someone shared with you — for example, linking an order to a customer. One lookup can also narrow another, so picking a country leaves only its cities to choose from.",
  calculated: "A value worked out automatically from your other columns using a formula, such as qty * price. You never type into it — see “Calculated columns: writing a formula” below.",
};

export function GuidePage() {
  return (
    <div className="flex flex-col gap-6" style={{ maxWidth: 820 }}>
      <Breadcrumbs items={[{ label: "Guide" }]} />

      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">How to use this app</h1>
        <p style={{ color: "var(--text-muted)" }}>
          A friendly, step-by-step guide — from your very first workspace to
          sharing, filtering, and formulas. No technical background needed. If you
          can use a spreadsheet, you can use this.
        </p>
      </header>

      {/* Contents */}
      <nav
        className="rounded p-4"
        style={{
          background: "var(--surface)",
          border: "var(--border-width) solid var(--border)",
        }}
        aria-label="Guide contents"
      >
        <div className="font-semibold mb-2">On this page</div>
        <ol className="flex flex-col gap-1" style={{ listStyle: "none", padding: 0, margin: 0 }}>
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

      <Section id="what-it-is" title="What this app is">
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
      </Section>

      <Section id="signing-in" title="Signing in">
        <p>
          There is no public sign-up. An administrator creates your account and gives
          you a <strong>username</strong> and <strong>password</strong>. On the
          sign-in screen, type them in and click <Ui>Sign in</Ui>. That’s it — you
          land on your home page.
        </p>
        <p>
          If you don’t have an account yet, ask whoever runs this app for one (see{" "}
          <a href="#people" style={{ color: "var(--brand)" }}>
            People, groups, and your profile
          </a>
          ).
        </p>
      </Section>

      <Section id="getting-around" title="Getting around">
        <p>Once you’re in, the bar across the top is your map:</p>
        <ul className="flex flex-col gap-2">
          <li>
            The name on the left (<Ui>Database w/ UI</Ui>) always takes you back to
            your <Link to="/" style={{ color: "var(--brand)" }}>home dashboard</Link>.
          </li>
          <li>
            <Ui>Workspaces</Ui> lists your folders, <Ui>People</Ui> shows accounts and
            groups, and <Ui>Guide</Ui> brings you here.
          </li>
          <li>
            The circle at the far right is <strong>your account</strong> — it shows
            your initials. Click it to see who you are signed in as, and to reach{" "}
            <Ui>Profile</Ui> (your own details) or <Ui>Log out</Ui>.
          </li>
          <li>
            The two small icons in the same bar switch the look between light and dark
            and between colour themes — see{" "}
            <a href="#appearance" style={{ color: "var(--brand)" }}>Appearance</a>.
          </li>
          <li>
            The trail of links under the title (the <em>breadcrumbs</em>) shows where
            you are and lets you step back up.
          </li>
        </ul>
        <p>
          Your <Link to="/" style={{ color: "var(--brand)" }}>home page</Link> is a
          dashboard: how many workspaces you have and how many lists other people
          have shared with you, then cards for your most recent workspaces and those
          shared lists, so you can jump straight back into what you were doing.
          Underneath, <Ui>Explore</Ui> links to the main corners of the app.
        </p>
        <Callout tone="tip" title="On a phone?">
          On a small screen the top links fold into a menu — tap the ☰ button on the
          right to open a drawer with your workspaces, their lists, and everything
          else. Your account is at the top of that drawer, with <Ui>Log out</Ui> just
          beneath it, so there is no separate circle to look for.
        </Callout>
      </Section>

      <Section id="workspace" title="Step 1 · Make a workspace">
        <p>
          A workspace is just a folder to keep related lists together. Let’s make one
          to hold our Contacts list.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Go to <Ui>Workspaces</Ui> (from the top bar, or the card on your home
            page).
          </li>
          <li>
            In the <Ui>New workspace name</Ui> box, type a name — say{" "}
            <em>Sales</em>.
          </li>
          <li>
            Click <Ui>Create</Ui>. Your new workspace appears in the list below.
          </li>
          <li>Click its name to open it.</li>
        </ol>
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
            In the <Ui>New list name</Ui> box, type <em>Contacts</em> and click{" "}
            <Ui>Create</Ui>.
          </li>
          <li>Click the list’s name to open it.</li>
        </ol>
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
            the menu (for a name, <Ui>Single line text</Ui>).
          </li>
          <li>
            Fill in any extra settings that appear for that type, then click{" "}
            <Ui>Add</Ui>.
          </li>
        </ol>
        <p>For our Contacts list, you might add:</p>
        <ul className="flex flex-col gap-1">
          <li>
            <em>Full name</em> — <Ui>Single line text</Ui>, and tick <Ui>Required</Ui>{" "}
            so no contact is nameless.
          </li>
          <li>
            <em>Email</em> — <Ui>Single line text</Ui>, and tick <Ui>Unique</Ui> so
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
            Have date, time, or user columns stamp themselves automatically when a
            record is created or edited.
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
        <Callout tone="warn" title="A type is forever">
          You can rename a column and change its other settings any time, but you{" "}
          <strong>cannot change its type</strong> once it exists, because your records
          already hold values in that shape. If you need a different type, add a new
          column instead. And deleting a column doesn’t erase what records held — the
          old values are simply hidden.
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
      </Section>

      <Section id="formulas" title="Calculated columns: writing a formula">
        <p>
          A <Ui>Calculated (formula)</Ui> column doesn’t hold anything you type. It
          holds a <strong>formula</strong>, and its value is worked out from the
          other columns of the same record every time you look at it. Change a{" "}
          <em>Quantity</em>, and the <em>Total</em> beside it is already right —
          there is nothing to refresh and nothing to keep in step.
        </p>
        <p>
          Add one under <Ui>Settings</Ui> → <Ui>Columns</Ui> → <Ui>Add column</Ui>,
          pick the type, and type the formula into the <Ui>Formula</Ui> box. The
          dialog lists, underneath, the columns you’re allowed to name.
        </p>

        <h3 className="font-semibold pt-1">Naming your columns</h3>
        <p>
          A formula refers to a column by its <strong>short name</strong> — the one
          shown in brackets beside it in the columns list, like{" "}
          <Code>unit_price</Code> — not by its pretty display name. Only three kinds
          of column can be named: <strong>Number</strong>,{" "}
          <strong>Single line text</strong>, and <strong>Long text</strong>. Dates,
          choices, tick boxes, people, and linked columns can’t be used in a
          formula yet, and neither can another calculated column.
        </p>

        <h3 className="font-semibold pt-1">Doing sums</h3>
        <p>
          Use <Code>+</Code>, <Code>-</Code>, <Code>*</Code> (times),{" "}
          <Code>/</Code> (divided by) and brackets, exactly as you would on paper:{" "}
          <Code>(qty * price) - discount</Code>. These work on numbers only — to
          join words together see <Code>CONCAT</Code> below.
        </p>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>min(a, b, …)</Code> — the smallest of the values given.
          </li>
          <li>
            <Code>max(a, b, …)</Code> — the largest.
          </li>
          <li>
            <Code>round(number)</Code> or <Code>round(number, places)</Code> —{" "}
            <Code>round(total, 2)</Code> gives you pence or cents.
          </li>
          <li>
            <Code>abs(number)</Code> — the value without its minus sign.
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

        <h3 className="font-semibold pt-1">A few worked examples</h3>
        <ul className="flex flex-col gap-1">
          <li>
            <Code>round(qty * unit_price, 2)</Code> — a line total, to two decimal
            places.
          </li>
          <li>
            <Code>CONCAT(TRIM(first_name), " ", UPPER(LEFT(last_name, 1)), ".")</Code>{" "}
            — <em>Ada L.</em>
          </li>
          <li>
            <Code>IF(LEN(notes) &gt; 0, "Has notes", "—")</Code> — a tidy flag
            column.
          </li>
          <li>
            <Code>max(0, total - paid)</Code> — what’s still owed, never negative.
          </li>
        </ul>

        <Callout tone="warn" title="What a calculated column can’t do">
          <ul className="flex flex-col gap-1 m-0">
            <li>
              It has no stored value, so you can’t type into it, give it a{" "}
              <Ui>Default value</Ui>, or mark it <Ui>Required</Ui> or{" "}
              <Ui>Unique</Ui>.
            </li>
            <li>
              You can’t sort, filter, or search on it — those work on saved values,
              and this one is worked out fresh each time.
            </li>
            <li>
              Exports leave it out, and a value for it in an imported file is
              ignored, so a file this app writes always imports cleanly back in.
            </li>
          </ul>
        </Callout>
        <Callout tone="tip" title="A blank cell means “not yet”">
          If any column the formula names is empty on that record, the result is
          blank (—) rather than zero — an unfinished row should look unfinished.
          Dividing by zero comes out blank the same way, so one odd record never
          spoils the rest of the list.
        </Callout>
        <Callout tone="warn" title="Renaming breaks a formula quietly">
          Formulas find columns by their short name. If you rename a column a
          formula uses, the formula stops finding it and those cells simply go
          blank — no error. After renaming, open the calculated column and update
          the formula to match.
        </Callout>
      </Section>

      <Section id="qr" title="Add records with a QR code">
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
            so you can check them first, or <Ui>Submit using QR code</Ui> to save the
            record straight away.
          </li>
          <li>
            Point your camera at the code. No camera? Click <Ui>Scan an image</Ui> and
            pick a saved picture of the code instead.
          </li>
        </ol>
        <ul className="flex flex-col gap-1">
          <li>
            The code has to hold the record’s fields (by their column names) and
            values. A code that holds something else — a website, Wi-Fi details — is
            politely refused.
          </li>
          <li>
            <Ui>Submit using QR code</Ui> saves even if a value is wrong; if the app
            rejects it, nothing is lost — the values reappear in the form with the
            reason shown, so you can fix and resave.
          </li>
        </ul>
      </Section>

      <Section id="find" title="Find records: search, sort, group, filter">
        <p>
          As a list grows, four tools help you find what you need. They live in the
          bar above your records.
        </p>
        <ul className="flex flex-col gap-2">
          <li>
            <strong>Search</strong> — type in <Ui>Search records…</Ui> to narrow to
            rows that match. It searches the whole list, not just the page you’re on.
          </li>
          <li>
            <strong>Sort</strong> — pick a column in <Ui>Sort by…</Ui> and use the
            arrow to flip between A→Z and Z→A. (On a wide screen you can also click a
            column heading.)
          </li>
          <li>
            <strong>Group</strong> — choose a column in <Ui>Group by…</Ui> to bunch
            rows with the same value together (all <em>Customers</em> together, say).
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
        <p style={{ color: "var(--text-muted)" }}>
          The value box adapts to the column: numbers get a number box, dates get a
          calendar, and choice/people/linked columns give you a picker. Comparisons
          like <Ui>is empty</Ui> need no value at all.
        </p>
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
            To make one, click <Ui>New view</Ui>, type a <Ui>View name</Ui>, and click{" "}
            <Ui>Create</Ui>.
          </li>
          <li>
            Set up the sort, filter, grouping, and columns you want, then click{" "}
            <Ui>Save view</Ui> to store them into the current view.
          </li>
        </ol>
        <ul className="flex flex-col gap-1">
          <li>
            Use <Ui>Columns</Ui> to tick which columns appear, and{" "}
            <Ui>Move up</Ui> / <Ui>Move down</Ui> to reorder them.
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
            Reorder fields with <Ui>Move up</Ui> / <Ui>Move down</Ui>, and give a
            field a friendlier <Ui>Label</Ui> just for this form.
          </li>
          <li>Click <Ui>Save</Ui> to apply. New fields start out shown and editable.</li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          The four automatic columns — <Ui>Created at</Ui>, <Ui>Updated at</Ui>,{" "}
          <Ui>Created by</Ui>, <Ui>Updated by</Ui> — are the exception: they start
          hidden and not editable, since the app maintains them. Tick <Ui>Shown</Ui>{" "}
          if you want them visible while someone fills the form in.
        </p>
      </Section>

      <Section id="sharing" title="Sharing and permissions">
        <p>
          Nothing is shared until you say so. You can share a whole workspace, a
          single list, an individual record, or a form — and control exactly what each
          person or group may do.
        </p>
        <ol className="flex flex-col gap-2">
          <li>
            Open sharing for the item (the <Ui>Share</Ui> button on a workspace or
            list row; inside <Ui>Settings</Ui> → <Ui>Permissions</Ui> for a list).
          </li>
          <li>
            Under <Ui>Add people or groups</Ui>, type a name to find the person or
            group.
          </li>
          <li>
            Give them access — either a one-click role (<Ui>Viewer</Ui>,{" "}
            <Ui>Editor</Ui>, or <Ui>Manager</Ui>) or tick the exact actions you want.
          </li>
          <li>
            Optionally set an <Ui>Expires (optional)</Ui> date so access ends by
            itself. Then click <Ui>Add</Ui>.
          </li>
        </ol>
        <p>The individual actions mean:</p>
        <ul className="flex flex-col gap-1">
          <li><strong>View</strong> — see the item and its records.</li>
          <li><strong>Create</strong> — add records.</li>
          <li><strong>Edit</strong> — change records.</li>
          <li><strong>Delete</strong> — remove records or the item.</li>
          <li><strong>Configure</strong> — change settings, columns, forms, and views.</li>
          <li><strong>Share</strong> — manage who has access.</li>
          <li><strong>Manage</strong> — full control (includes all of the above).</li>
        </ul>
        <p style={{ color: "var(--text-muted)" }}>
          The roles are just shortcuts: <em>Viewer</em> = View; <em>Editor</em> =
          View, Create, Edit, Delete; <em>Manager</em> = full control.
        </p>
        <Callout tone="tip" title="Good to know">
          The owner of an item always keeps full access. A row shows a friendly
          reminder like <Ui>Expires today</Ui> when access is ending, and{" "}
          <Ui>Expired</Ui> once it has. For a list you can turn on{" "}
          <Ui>Unique permissions</Ui> to ignore access handed down from its workspace
          and use only the people you add here. Sharing with a group grants everyone
          in it — a quick way to give a whole team access at once.
        </Callout>
        <Callout tone="tip" title="Sharing with everybody">
          One group, <Ui>Authenticated Users</Ui>, is built in and always means
          “everyone with an account here”, including people who join later. Share
          with it when something is meant for the whole organisation. On the{" "}
          <Ui>People</Ui> page it looks like a group with no members: that’s
          expected, since membership is automatic rather than a list someone
          maintains. For the same reason the app won’t let anyone rename, edit, or
          delete it.
        </Callout>
      </Section>

      <Section id="transfer" title="Moving and giving a list away">
        <p>
          A list doesn’t have to stay where you made it. Open a workspace to see its
          lists; each one has a <Ui>Move or transfer</Ui> button (the paper-plane
          icon), alongside Share, Rename, and Delete.
        </p>
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
        <Callout tone="warn" title="Transferring is one-way">
          After you transfer a list it belongs to the other person; getting it back means
          asking them to transfer or share it to you. If you only want someone to help
          with a list, <a href="#sharing" style={{ color: "var(--brand)" }}>share it</a>{" "}
          with them instead of transferring it.
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
        <ul className="flex flex-col gap-1">
          <li>
            Administrators can <Ui>Add user</Ui> (username, email, password), reset
            passwords, mark accounts active or inactive, and grant administrator
            rights.
          </li>
          <li>
            They can also create groups (<Ui>New group</Ui>) and choose their members —
            handy for sharing with a whole team in one go.
          </li>
          <li>
            Everyone has a <Link to="/profile" style={{ color: "var(--brand)" }}>
              Profile
            </Link>{" "}
            page for their own name and details, reached from the account circle at
            the top right. Filling it in is optional and doesn’t affect signing in.
            The name you put there is what your initials in that circle come from.
          </li>
        </ul>
        <Callout tone="tip" title="Changing your password">
          When you change your own password you’ll be asked for your current one too.
          People are always shown to others by their username, never their personal
          name, to keep private details private.
        </Callout>
      </Section>

      <Section id="import-export" title="Import and export">
        <p>
          You don’t have to type everything. Open a list and click{" "}
          <Ui>Import / Export</Ui>.
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
          <li>
            Click <Ui>Export records</Ui> to download the file. Or click{" "}
            <Ui>Download template</Ui> to get an empty file with just the headings,
            ready to fill in.
          </li>
        </ol>
        <p>
          <strong>To bring data in:</strong> under <Ui>Import records</Ui>, click{" "}
          <Ui>Choose file</Ui> and pick a <em>.csv</em> or <em>.json</em> file. After
          it uploads you’ll see how many records were added and, if any rows were
          skipped, the reason for each.
        </p>
        <Callout tone="warn" title="Match the headings">
          Your file’s column headings must match your list’s underlying column names
          (the short name shown in brackets next to each column), not necessarily the
          pretty display names. Headings the list doesn’t recognise are simply
          ignored, and a bad row is skipped while the good rows still import — nothing
          all-or-nothing.
        </Callout>
      </Section>

      <Section id="bin" title="Recovering things you deleted">
        <p>
          Deleting doesn’t destroy anything straight away. Records, lists, and whole
          workspaces each go to a <strong>bin</strong> you can fetch them back from.
        </p>
        <ul className="flex flex-col gap-2">
          <li>
            <strong>A record.</strong> Open the list and click{" "}
            <Ui>Deleted records</Ui>.
          </li>
          <li>
            <strong>A list.</strong> Open the workspace it was in and click{" "}
            <Ui>Deleted lists</Ui>.
          </li>
          <li>
            <strong>A workspace.</strong> Go to <Ui>Workspaces</Ui> and click{" "}
            <Ui>Deleted workspaces</Ui>.
          </li>
        </ul>
        <p>
          Each shows what was removed and when. Click <Ui>Restore</Ui> beside an
          entry to bring it back, with everything it contained. Beside that sits a
          red bin icon, <Ui>Delete permanently</Ui>, for when you want something
          gone now rather than at the end of its window — it asks you to confirm,
          and there is no undo.
        </p>
        <Callout tone="warn" title="There’s a time limit">
          Deleted things are only kept for a while (a retention window). Once that
          passes they’re gone for good and can’t be restored — so recover anything
          important soon.
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
      </Section>

      <Section id="appearance" title="Appearance and themes">
        <p>
          The two small icons near the top right change how the app looks. One flips
          between <strong>light</strong> and <strong>dark</strong>; the other picks a
          colour theme (there are several, from a clean modern look to a bold
          high-contrast one). Your choice is remembered on your device — pick whatever
          is easiest on your eyes.
        </p>
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
            <strong>I can’t sort or filter by my formula column.</strong> Calculated
            values aren’t stored, they’re worked out as you look — so there’s
            nothing for sorting, filtering, or search to match on.
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
