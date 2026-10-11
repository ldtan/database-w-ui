import { Button } from "primereact/button";
import { Link } from "react-router-dom";

import { AppDialog } from "../../components/AppDialog";

/**
 * The first thing a new account sees, once and once only.
 *
 * It answers "what is this and where do I start?" in four words and a sentence
 * each, then offers the tour. Everything on it is escapable — a person who came
 * here to do a job can dismiss it and never see it again, and the help button
 * they were about to be shown is still there when they change their mind.
 */

const WORDS: { icon: string; word: string; text: string }[] = [
  {
    icon: "pi pi-th-large",
    word: "Workspace",
    text: "A folder that holds related tables.",
  },
  {
    icon: "pi pi-table",
    word: "Table",
    text: "One set of records sharing the same columns — the heart of it all.",
  },
  {
    icon: "pi pi-bars",
    word: "Column",
    text: "One detail every record holds, like Email.",
  },
  {
    icon: "pi pi-file",
    word: "Record",
    text: "One row: a contact, a task, an item.",
  },
];

interface Props {
  visible: boolean;
  /** The name to greet, when there is one worth using. */
  name?: string;
  onTakeTour: () => void;
  onDismiss: () => void;
}

export function WelcomeDialog({ visible, name, onTakeTour, onDismiss }: Props) {
  return (
    <AppDialog
      visible={visible}
      onHide={onDismiss}
      header={`Welcome${name ? `, ${name}` : ""} 👋`}
      style={{ width: 560 }}
      // Dismissing by clicking away is the same answer as "Not now", and this
      // dialog is asked once — there is no state to lose by getting out of it.
      dismissableMask
      footer={
        <>
          <Button label="Not now" text onClick={onDismiss} />
          <Link to="/guide" className="no-underline" onClick={onDismiss}>
            <Button label="Read the guide" icon="pi pi-book" outlined />
          </Link>
          <Button label="Show me around" icon="pi pi-compass" onClick={onTakeTour} />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p style={{ color: "var(--text-muted)" }}>
          This app lets you build your own little databases through a screen — no
          code, no spreadsheets to email around. If you can use a spreadsheet, you
          can use this.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {WORDS.map((w) => (
            <div key={w.word} className="surface-card p-3 flex items-start gap-3">
              <span className="entity-icon" aria-hidden>
                <i className={w.icon} />
              </span>
              <span className="flex flex-col min-w-0">
                <span className="font-medium">{w.word}</span>
                <span className="text-sm" style={{ color: "var(--text-muted)" }}>
                  {w.text}
                </span>
              </span>
            </div>
          ))}
        </div>

        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          The tour takes about two minutes and stops wherever you like. Afterwards
          the <i className="pi pi-question-circle" /> button in the corner has it
          again, along with tips for whatever page you are on.
        </p>
      </div>
    </AppDialog>
  );
}
