import { useEffect, useState, type FormEvent } from "react";
import { Button } from "primereact/button";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";

import { AppDialog } from "./AppDialog";

interface Props {
  visible: boolean;
  onHide: () => void;
  header: string;
  label: string;
  placeholder?: string;
  /** What the caller will do with the entered name. */
  hint?: string;
  submitLabel?: string;
  loading?: boolean;
  /** The server's complaint about the last attempt, if there was one. */
  error?: string | null;
  onSubmit: (name: string) => void;
}

/**
 * "Give it a name", as a dialog.
 *
 * Three pages had an always-visible name box wired to a Create button sitting
 * above their content — a permanent form for something done once a week, taking
 * the most valuable strip of every page and making all three look like the
 * setup step of a wizard. Behind a button instead, the page is about what is in
 * it, and the naming is a real form: focused on open, submits on Enter, and
 * shows the server's error next to the field that caused it.
 */
export function CreateNameDialog({
  visible,
  onHide,
  header,
  label,
  placeholder,
  hint,
  submitLabel = "Create",
  loading,
  error,
  onSubmit,
}: Props) {
  const [name, setName] = useState("");

  // Cleared on open, not on close: a failed create keeps what you typed while
  // the dialog is up, and a fresh one never starts with the last thing you named.
  useEffect(() => {
    if (visible) setName("");
  }, [visible]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) onSubmit(name.trim());
  }

  return (
    <AppDialog
      header={header}
      visible={visible}
      onHide={onHide}
      style={{ width: 420 }}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        {error && <Message severity="error" text={error} />}
        <div className="flex flex-col gap-2">
          <label htmlFor="create-name" className="text-sm font-medium">
            {label}
          </label>
          <InputText
            id="create-name"
            value={name}
            placeholder={placeholder}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
          {hint && (
            <span className="text-sm" style={{ color: "var(--text-muted)" }}>
              {hint}
            </span>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button label="Cancel" text type="button" onClick={onHide} />
          <Button
            type="submit"
            label={submitLabel}
            icon="pi pi-plus"
            loading={loading}
            disabled={!name.trim()}
          />
        </div>
      </form>
    </AppDialog>
  );
}
