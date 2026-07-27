import type { ReactNode } from "react";
import { Checkbox } from "primereact/checkbox";

interface FieldProps {
  /** Omit for a control that labels itself (a checkbox, a button row). */
  label?: ReactNode;
  /** The control's id, so clicking the label focuses it. PrimeReact controls
   *  take `inputId` rather than `id` — pass whichever one that control uses. */
  htmlFor?: string;
  /** What the field means, or what changing it costs. Under the control, not
   *  above it: it is read after the thing it describes, if at all. */
  hint?: ReactNode;
  /** Says so in the label rather than in the placeholder, which disappears the
   *  moment anybody types. */
  optional?: boolean;
  error?: string | null;
  children: ReactNode;
}

/**
 * One labelled control.
 *
 * Every dialog had grown its own version of this — `<div className="flex
 * flex-col gap-2">`, a `<label>`, the control, and sometimes a `<small
 * style={{ color: "var(--text-muted)" }}>` — at gap-1 in some places and gap-2
 * in others, with hints above the control as often as below. Thirty of those in
 * a column is why the forms read as a wall: nothing in them is bigger or
 * smaller than anything else, so there is no shape to skim.
 */
export function Field({
  label,
  htmlFor,
  hint,
  optional,
  error,
  children,
}: FieldProps) {
  return (
    <div className="field">
      {label && (
        <label className="field-label" htmlFor={htmlFor}>
          {label}
          {optional && <span className="field-optional">Optional</span>}
        </label>
      )}
      {children}
      {hint && <p className="field-hint">{hint}</p>}
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

/** Two fields side by side, stacking on a narrow dialog. For pairs that are read
 *  together and are short — Min and Max, a name and its slug. */
export function FieldRow({ children }: { children: ReactNode }) {
  return <div className="field-row">{children}</div>;
}

/**
 * A titled group of fields.
 *
 * The dialogs' section headings used to be the same `text-sm font-medium` as
 * their field labels, so "Export options" and "Limit" carried identical weight
 * and the grouping existed only in the author's head.
 */
export function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="form-section">
      <div className="form-section-head">
        <h3 className="form-section-title">{title}</h3>
        {description && <p className="field-hint">{description}</p>}
      </div>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  );
}

/**
 * A checkbox with its label beside it, and its explanation under both.
 *
 * The label is part of the control's target rather than text sitting next to
 * it, which is the difference between a 16px hit area and a 200px one.
 */
export function CheckboxField({
  inputId,
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  inputId: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <div className="check-field">
      <Checkbox
        inputId={inputId}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(!!e.checked)}
      />
      <div className="flex flex-col gap-0.5 min-w-0">
        <label htmlFor={inputId} className="check-field-label">
          {label}
        </label>
        {hint && <p className="field-hint">{hint}</p>}
      </div>
    </div>
  );
}
