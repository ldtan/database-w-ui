import { useEffect, useState, type FormEvent } from "react";
import { Button } from "primereact/button";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";

import { AppDialog } from "./AppDialog";
import { Field } from "./Field";

// The footer's submit button lives outside the <form>, so the two are tied
// together by id rather than by nesting.
const FORM_ID = "create-name-form";

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
      footer={
        <>
          <Button label="Cancel" text type="button" onClick={onHide} />
          {/* In the dialog's footer, which is outside the <form> element — so it
              says which form it submits rather than relying on being inside one. */}
          <Button
            type="submit"
            form={FORM_ID}
            label={submitLabel}
            icon="pi pi-plus"
            loading={loading}
            disabled={!name.trim()}
          />
        </>
      }
    >
      <form id={FORM_ID} onSubmit={submit} className="flex flex-col gap-4">
        {error && <Message severity="error" text={error} />}
        <Field label={label} htmlFor="create-name" hint={hint}>
          <InputText
            id="create-name"
            value={name}
            placeholder={placeholder}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </Field>
      </form>
    </AppDialog>
  );
}
