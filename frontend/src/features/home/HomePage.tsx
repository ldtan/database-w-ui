import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "primereact/button";

import { COLUMN_TYPE_OPTIONS } from "../../lib/columnTypes";
import { Figure } from "../guide/Figure";
import { SECTIONS } from "../guide/sections";

/**
 * The public home page: what this app is, for somebody who has just arrived at the
 * URL and has no account.
 *
 * Only reachable when a deployment sets `HOME_ENABLED` (`lib/config.ts`), and loaded
 * lazily, so an instance that runs this for one team never fetches it. Like the
 * guide, it is a static document — nothing here is behind a request, which is what
 * lets it render for a visitor with no token at all.
 *
 * Three rules it is written to, each of which has a way of going wrong:
 *
 * - **It claims only what is built.** There is no public sign-up — accounts are
 *   minted by whoever runs the deployment (`docs/known-gaps.md` §1) — so the call to
 *   action is "Log in" and the page says plainly where an account comes from. A
 *   "Sign up free" button here would be a dead end, and the day registration ships
 *   is the day to add one.
 * - **It borrows rather than restates.** The pitch is the guide's own
 *   `what-it-is`/`words` sections, the screenshot is a guide figure, and the column
 *   table is generated from `COLUMN_TYPE_OPTIONS` — the same table the "Add column"
 *   menu reads, so a new type appears here without anybody remembering to add it.
 *   What is left hand-written is only the ordering and the summaries below, and
 *   those are the only sentences that can go stale.
 * - **It links into the guide instead of teaching.** Every card points at the
 *   section that explains it, because the guide is twenty-six sections of the same
 *   material written properly and a landing page is not where to re-attempt it.
 *   The anchors have to exist in `features/guide/sections.ts`; the guide's own test
 *   checks its internal links, not these.
 */

/** One of the five words the whole app is built from. */
function Word({
  icon,
  word,
  children,
}: {
  icon: string;
  word: string;
  children: ReactNode;
}) {
  return (
    <li className="surface-card flex items-start gap-3 p-4">
      <span className="entity-icon" aria-hidden>
        <i className={icon} />
      </span>
      <span className="flex flex-col gap-1">
        <span className="font-semibold">{word}</span>
        <span className="text-sm" style={{ color: "var(--text-muted)" }}>
          {children}
        </span>
      </span>
    </li>
  );
}

/** One thing the app does, and the guide section that explains it. `to` carries the
 *  anchor, so the card is a way into the document rather than a summary of it. */
function Capability({
  icon,
  title,
  to,
  children,
}: {
  icon: string;
  title: string;
  to: string;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      className="surface-card is-interactive flex flex-col gap-2 p-4 no-underline"
      style={{ color: "var(--text)" }}
    >
      <span className="flex items-center gap-2 font-semibold">
        <i className={icon} style={{ color: "var(--brand)" }} aria-hidden />
        {title}
      </span>
      <span className="text-sm" style={{ color: "var(--text-muted)" }}>
        {children}
      </span>
    </Link>
  );
}

export function HomePage() {
  return (
    <div>
      <section className="home-hero">
        <div className="home-section flex flex-col gap-8">
          <div className="flex flex-col gap-4" style={{ maxWidth: "40rem" }}>
            <span className="home-eyebrow">Self-hostable · no code</span>
            <h1
              className="text-3xl sm:text-4xl font-semibold"
              style={{
                fontFamily: "var(--font-family-heading)",
                letterSpacing: "var(--heading-tracking)",
                lineHeight: 1.15,
              }}
            >
              Build your own little databases through a screen
            </h1>
            <p className="text-lg" style={{ color: "var(--text-muted)" }}>
              A tidy, shareable set of tables that live in one place — you decide
              what each table holds and who may see or change it. No code, no
              spreadsheets emailed around.
            </p>
            <p style={{ color: "var(--text-muted)" }}>
              People use it for a contact book, an inventory, a task tracker, a
              guest table, a simple CRM — anything that is really “a table of
              things, each with the same set of details”.
            </p>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <Link to="/login" className="no-underline">
                <Button label="Log in" icon="pi pi-sign-in" size="large" />
              </Link>
              <Link to="/guide" className="no-underline">
                <Button
                  label="Read the guide"
                  icon="pi pi-book"
                  size="large"
                  outlined
                />
              </Link>
            </div>
          </div>

          {/* A real screenshot of the real app, taken by `capture:guide` against a
              running instance — the strongest thing this page can say, and it costs
              one import. It follows the reader's light/dark choice, so the picture
              and the page around it are never in different modes. */}
          <Figure name="shell" />
        </div>
      </section>

      <section className="home-section flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <span className="home-eyebrow">The whole idea</span>
          <h2 className="text-2xl font-semibold">Five words to know</h2>
          <p style={{ color: "var(--text-muted)" }}>
            If you can use a spreadsheet you can use this: a table is a sheet, a
            column is a column, a record is a row — with the app keeping the rules
            tidy for you.
          </p>
        </div>
        <ul
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          <Word icon="pi pi-th-large" word="Workspace">
            A folder that holds related tables — a “Sales” workspace, say.
          </Word>
          <Word icon="pi pi-table" word="Table">
            One set of records sharing the same columns, like “Contacts”. This is
            the heart of the app.
          </Word>
          <Word icon="pi pi-table" word="Column">
            One kind of detail every row records — “Email”, “Status” — and you
            choose what type of information it holds.
          </Word>
          <Word icon="pi pi-file" word="Record">
            One row: a single contact, task or item, with a value in each column.
          </Word>
          <Word icon="pi pi-eye" word="View">
            A saved way of looking at a table — which rows show, how they are
            sorted and grouped, which columns appear.
          </Word>
          <Word icon="pi pi-book" word="…and the guide">
            {SECTIONS.length} sections, in plain language, with pictures of the
            real screens.{" "}
            <Link to="/guide" style={{ color: "var(--brand)" }}>
              Start reading
            </Link>
            .
          </Word>
        </ul>
      </section>

      <section className="home-section flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <span className="home-eyebrow">What you can do</span>
          <h2 className="text-2xl font-semibold">
            Everything a table needs, and nothing you have to configure first
          </h2>
          <p style={{ color: "var(--text-muted)" }}>
            Each of these has a section in the guide — click through to the one you
            came here wondering about.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <Capability icon="pi pi-table" title="Typed columns" to="/guide#columns">
            {COLUMN_TYPE_OPTIONS.length} kinds of column, from a tick box to a
            phone number to a point on a map — each with the right input, and
            checked when it is saved.
          </Capability>
          <Capability icon="pi pi-filter" title="Find anything" to="/guide#find">
            Search, sort, group and filter, with conditions you can stack and
            nest. Save the result as a view.
          </Capability>
          <Capability icon="pi pi-eye" title="Views" to="/guide#views">
            A saved way of looking at a table, so the shape you work in is one click
            away instead of rebuilt every morning.
          </Capability>
          <Capability icon="pi pi-pencil" title="Forms" to="/guide#forms">
            Shape the screen people fill in — order the fields, group them, and
            show one only when another has a particular answer.
          </Capability>
          <Capability icon="pi pi-chart-bar" title="Metrics" to="/guide#metrics">
            Totals, averages and counts along the top of a table, each one filtered
            however you like.
          </Capability>
          <Capability icon="pi pi-share-alt" title="Sharing" to="/guide#sharing">
            Give a person or a group exactly what they need — a whole workspace, a
            single table, or one record — as view, edit or manage.
          </Capability>
          <Capability icon="pi pi-link" title="Linked tables" to="/guide#nested-lookups">
            Point a column at a row in another table, and let one choice narrow the
            next: pick a country and only its cities remain.
          </Capability>
          <Capability icon="pi pi-calculator" title="Formulas" to="/guide#formulas">
            A column worked out from the others — <code>qty * price</code> — that
            sorts, filters and totals like any number.
          </Capability>
          <Capability icon="pi pi-qrcode" title="QR capture" to="/guide#qr">
            Fill a record from a QR code, or run a scanning session for a queue of
            people holding badges.
          </Capability>
          <Capability
            icon="pi pi-file-import"
            title="Import and export"
            to="/guide#import-export"
          >
            CSV or JSON, both ways, keyed by column name — so what goes out is what
            comes back in.
          </Capability>
          <Capability icon="pi pi-trash" title="A bin for mistakes" to="/guide#bin">
            Deleting puts things in a bin you can recover from, not past the
            horizon.
          </Capability>
          <Capability icon="pi pi-palette" title="Themes" to="/guide#appearance">
            Four palettes, light and dark, remembered against your account rather
            than this browser.
          </Capability>
        </div>
      </section>

      <section className="home-section flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <span className="home-eyebrow">Columns</span>
          <h2 className="text-2xl font-semibold">
            A column holds what it means, not just text
          </h2>
          <p style={{ color: "var(--text-muted)" }}>
            The type you pick decides how a cell is filled in, how it is checked,
            how it sorts and what you can filter it by. These are all of them —
            each explained in{" "}
            <Link to="/guide#column-types" style={{ color: "var(--brand)" }}>
              the guide
            </Link>
            .
          </p>
        </div>
        {/* Generated from the same table the "Add column" menu reads, so this cannot
            fall behind the app: a type that ships appears here, and one that is
            removed disappears. The guide uses the identical trick for its glossary. */}
        <ul
          className="flex flex-wrap gap-2"
          style={{ listStyle: "none", padding: 0, margin: 0 }}
        >
          {COLUMN_TYPE_OPTIONS.map((option) => (
            <li
              key={option.value}
              className="rounded px-2.5 py-1 text-sm"
              style={{
                background: "var(--surface-2)",
                border: "var(--border-width) solid var(--border)",
              }}
            >
              {option.label}
            </li>
          ))}
        </ul>
      </section>

      <section className="home-section">
        <div className="hero flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <h2 className="text-2xl font-semibold">Have a look around</h2>
            <p style={{ color: "var(--text-muted)", maxWidth: "44rem" }}>
              The guide walks the whole app from your first workspace to sharing,
              filtering and formulas, with a picture of every screen — and you can
              read all of it before you sign in.
            </p>
            {/* Said plainly rather than left for somebody to discover at the login
                form. There is no public registration yet (known-gaps §1); when there
                is, this paragraph and the buttons below it are what change. */}
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              There is no public sign-up: accounts are created by whoever runs this
              deployment. If you need one, ask them — and if that is you, the{" "}
              <a
                href="https://github.com/ldtan/database-w-ui"
                style={{ color: "var(--brand)" }}
                target="_blank"
                rel="noreferrer"
              >
                repository
              </a>{" "}
              has the instructions for running your own.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to="/guide" className="no-underline">
              <Button label="Read the guide" icon="pi pi-book" />
            </Link>
            <Link to="/login" className="no-underline">
              <Button label="Log in" icon="pi pi-sign-in" outlined />
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
