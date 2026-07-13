import { useState } from "react";
import { InputText } from "primereact/inputtext";

import { isValidName, slugify } from "../lib/slug";

export interface NamePair {
  displayName: string;
  name: string;
}

interface Props {
  /** Prefixes the input ids, so two of these can share a page. */
  idPrefix: string;
  value: NamePair;
  onChange: (next: NamePair) => void;
  /** Start with the API name following the label. True when creating (nobody has
   *  used the name yet); false when editing (the name is already out there). */
  linked: boolean;
  autoFocus?: boolean;
}

/**
 * The two identifiers every list, column, view and form carries: the label the UI
 * shows, and the `name` — a slug that code addresses the resource by, in export
 * headers and in API paths (`/lists/{list}/columns/first_name`).
 *
 * While creating, the name follows the label, which is what the app did silently
 * before and is right almost always. The moment it is typed in, it stops following:
 * a name someone chose is not something a later keystroke in another field should
 * quietly rewrite. Editing an existing resource starts unlinked for the same reason.
 *
 * Reset by remounting — give it a `key` (the resource's uuid, or "new") so opening
 * the dialog on a different resource starts from that resource's state.
 */
export function NameFields({
  idPrefix,
  value,
  onChange,
  linked: linkedInitially,
  autoFocus,
}: Props) {
  const [linked, setLinked] = useState(linkedInitially);
  const invalid = value.name.length > 0 && !isValidName(value.name);

  return (
    <>
      <div className="flex flex-col gap-2">
        <label htmlFor={`${idPrefix}-display-name`}>Name</label>
        <InputText
          id={`${idPrefix}-display-name`}
          value={value.displayName}
          onChange={(e) =>
            onChange({
              displayName: e.target.value,
              name: linked ? slugify(e.target.value) : value.name,
            })
          }
          autoFocus={autoFocus}
        />
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor={`${idPrefix}-name`}>API name</label>
        <InputText
          id={`${idPrefix}-name`}
          value={value.name}
          onChange={(e) => {
            setLinked(false);
            onChange({ displayName: value.displayName, name: e.target.value });
          }}
          invalid={invalid}
          aria-describedby={`${idPrefix}-name-help`}
        />
        <small id={`${idPrefix}-name-help`} style={{ color: invalid ? "var(--red-500)" : "var(--text-muted)" }}>
          {invalid
            ? "Start with a lowercase letter; then lowercase letters, digits and underscores only."
            : "How code refers to this: export headers and API paths. Changing it breaks anything using the old name."}
        </small>
      </div>
    </>
  );
}
