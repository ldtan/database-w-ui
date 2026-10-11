import { useState } from "react";
import { InputText } from "primereact/inputtext";

import { isValidName, slugify } from "../lib/slug";
import { Field } from "./Field";

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
 * The two identifiers every workspace, table, column, view and form carries: the
 * label the UI shows, and the `name` — a slug that code addresses the resource by.
 * What that means depends on the resource: a column's name keys an export's headers,
 * and a table's children can be addressed by name in API paths
 * (`/tables/{table}/columns/first_name`). Hence the deliberately general help text —
 * it must be true of a workspace as well as a column.
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
      <Field label="Name" htmlFor={`${idPrefix}-display-name`}>
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
      </Field>
      <Field
        label="API name"
        htmlFor={`${idPrefix}-name`}
        hint={
          invalid
            ? undefined
            : "The code-facing name. Changing it breaks anything that refers to this one by name."
        }
        error={
          invalid
            ? "Start with a lowercase letter; then lowercase letters, digits and underscores only."
            : undefined
        }
      >
        <InputText
          id={`${idPrefix}-name`}
          value={value.name}
          onChange={(e) => {
            setLinked(false);
            onChange({ displayName: value.displayName, name: e.target.value });
          }}
          invalid={invalid}
        />
      </Field>
    </>
  );
}
