import { useQuery } from "@tanstack/react-query";
import { Checkbox } from "primereact/checkbox";
import { Dropdown } from "primereact/dropdown";
import { Message } from "primereact/message";

import { api } from "../../lib/api";
import type { Column, MergeConfig } from "../../lib/types";

/**
 * Which of this list's columns can be a merged form's link.
 *
 * Mirrors `FormService._validate_merge`, and has to stay in step with it: a column
 * offered here and refused on save reads as a broken control. A `multiple` lookup has
 * no single linked record to write into, and a nested one's options depend on a
 * sibling cell that a merged create has not written yet when it needs the target.
 */
export function linkCandidates(columns: Column[]): Column[] {
  return columns.filter(
    (c) =>
      c.type === "lookup" &&
      !c.config.multiple &&
      !c.config.parent_column_uuid &&
      !!c.config.target_list_uuid,
  );
}

interface Props {
  columns: Column[];
  value: MergeConfig | null;
  disabled: boolean;
  onChange: (next: MergeConfig | null) => void;
}

/**
 * The "Linked list" half of a form: which lookup column reaches the other list, which
 * of its fields this form borrows, and whether several rows may share one linked
 * record.
 *
 * The borrowed fields are listed explicitly rather than defaulted to "all", matching
 * what the backend stores and for the same reason: the other list is not this form's
 * to enumerate, and "all of them" would silently widen as that list gained columns.
 */
export function MergeEditor({ columns, value, disabled, onChange }: Props) {
  const candidates = linkCandidates(columns);
  const link = columns.find((c) => c.uuid === value?.link_column_uuid) ?? null;
  const targetUuid = link?.config.target_list_uuid ?? null;

  // `retry: false`: a 403 here means the configurer cannot read the linked list, which
  // is exactly the case the backend refuses to save anyway. Say so rather than spin.
  const targetColumns = useQuery({
    queryKey: ["columns", targetUuid],
    queryFn: () => api.get<Column[]>(`/lists/${targetUuid}/columns/`),
    enabled: !!targetUuid,
    retry: false,
  });

  const borrowed = new Set((value?.fields ?? []).map((f) => f.column_uuid));

  function toggleField(uuid: string, on: boolean) {
    if (!value) return;
    const next = on
      ? [
          ...value.fields,
          {
            column_uuid: uuid,
            included: true,
            enabled: true,
            order: value.fields.length,
          },
        ]
      : value.fields.filter((f) => f.column_uuid !== uuid);
    onChange({ ...value, fields: next.map((f, i) => ({ ...f, order: i })) });
  }

  if (candidates.length === 0) {
    return (
      <Message
        severity="info"
        text="To merge another list into this form, add a lookup column pointing at it first. It must allow a single value and not be nested under another column."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <label className="field-label">Linked through</label>
        <Dropdown
          value={value?.link_column_uuid ?? null}
          options={[
            { label: "Not a merged form", value: null },
            ...candidates.map((c) => ({ label: c.display_name, value: c.uuid })),
          ]}
          disabled={disabled}
          onChange={(e) =>
            onChange(
              e.value
                ? {
                    link_column_uuid: e.value,
                    // Retargeting the link changes which list the borrowed uuids
                    // belong to, so they start empty rather than carrying over.
                    fields: [],
                    allow_shared: value?.allow_shared ?? false,
                  }
                : null,
            )
          }
          className="w-full sm:w-64"
        />
        <small className="field-hint">
          Submitting this form writes a record here and one in the linked list.
        </small>
      </div>

      {value && targetColumns.isError && (
        <Message
          severity="warn"
          text="You do not have access to the linked list, so its fields cannot be chosen here."
        />
      )}

      {value && targetColumns.data && (
        <div className="flex flex-col gap-1">
          <label className="field-label">Fields to include</label>
          {targetColumns.data.length === 0 && (
            <p className="field-hint">That list has no columns yet.</p>
          )}
          {targetColumns.data
            // A formula is a calculated column's only writer, so it can never be an
            // input — the same rule this form follows for its own list's columns.
            .filter((c) => c.type !== "calculated")
            .map((c) => (
              <div key={c.uuid} className="flex items-center gap-2">
                <Checkbox
                  inputId={`merge-${c.uuid}`}
                  checked={borrowed.has(c.uuid)}
                  disabled={disabled}
                  onChange={(e) => toggleField(c.uuid, !!e.checked)}
                />
                <label htmlFor={`merge-${c.uuid}`}>{c.display_name}</label>
              </div>
            ))}
        </div>
      )}

      {value && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="merge-allow-shared"
              checked={value.allow_shared}
              disabled={disabled}
              onChange={(e) =>
                onChange({ ...value, allow_shared: !!e.checked })
              }
            />
            <label htmlFor="merge-allow-shared">
              Let several rows share one linked record
            </label>
          </div>
          <small className="field-hint">
            {value.allow_shared
              ? "Editing a linked record changes every row pointing at it, and one edit rewrites all of them."
              : "Off means one row per linked record. Picking a record another row already uses will be refused."}
          </small>
        </div>
      )}
    </div>
  );
}
