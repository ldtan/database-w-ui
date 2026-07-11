import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { MultiSelect } from "primereact/multiselect";

import { api } from "../../lib/api";
import { displayText } from "../../lib/display";
import type { Column, ListItem, ListRead } from "../../lib/types";

interface TargetRows {
  isLoading: boolean;
  options: { label: string; value: string }[];
  labelFor: (uuid: string) => string;
}

// Fetch a target list's schema + rows so lookup values can be shown and picked
// by a chosen label column instead of a raw row uuid. The label column is the
// lookup's configured display column, falling back to the target's primary
// column, then its first column. Queries share the same cache keys the target
// list's own detail page uses, so they dedupe.
function useTargetRows(
  targetListUuid?: string | null,
  displayColumnUuid?: string | null,
): TargetRows {
  const enabled = !!targetListUuid;
  const list = useQuery({
    queryKey: ["list", targetListUuid],
    queryFn: () => api.get<ListRead>(`/lists/${targetListUuid}`),
    enabled,
  });
  const columns = useQuery({
    queryKey: ["columns", targetListUuid],
    queryFn: () => api.get<Column[]>(`/lists/${targetListUuid}/columns/`),
    enabled,
  });
  const items = useQuery({
    queryKey: ["items", targetListUuid, null],
    // Fetch up to the backend max so labels/picker cover large target lists,
    // not just the default first page.
    queryFn: () =>
      api.get<ListItem[]>(`/lists/${targetListUuid}/items/?limit=1000`),
    enabled,
  });

  const cols = columns.data;
  const primaryUuid = list.data?.primary_column?.uuid;
  const label = useMemo(() => {
    const list_ = cols ?? [];
    return (
      (displayColumnUuid && list_.find((c) => c.uuid === displayColumnUuid)) ||
      list_.find((c) => c.uuid === primaryUuid) ||
      list_[0] ||
      null
    );
  }, [cols, primaryUuid, displayColumnUuid]);

  const rows = items.data;
  // Build the id->label map and options once per data/label change, not once
  // per rendered cell (LookupCell is rendered per row).
  const { byId, options } = useMemo(() => {
    const map = new Map<string, string>();
    const opts: { label: string; value: string }[] = [];
    for (const item of rows ?? []) {
      const text = label ? displayText(label, item.data?.[label.uuid]) : "";
      const rowLabel = text || `Row ${item.uuid.slice(0, 8)}`;
      map.set(item.uuid, rowLabel);
      opts.push({ label: rowLabel, value: item.uuid });
    }
    return { byId: map, options: opts };
  }, [rows, label]);

  return {
    isLoading: list.isLoading || columns.isLoading || items.isLoading,
    options,
    labelFor: (uuid) => byId.get(uuid) ?? `Row ${uuid.slice(0, 8)}`,
  };
}

export function LookupCell({ column, value }: { column: Column; value: unknown }) {
  const target = column.config.target_list_uuid as string | undefined;
  const display = column.config.display_column_uuid as string | undefined;
  const { labelFor } = useTargetRows(target, display);
  const uuids = Array.isArray(value) ? (value as string[]) : value ? [String(value)] : [];
  if (uuids.length === 0) return <span className="text-gray-300">—</span>;
  return <span>{uuids.map(labelFor).join(", ")}</span>;
}

export function LookupField({
  column,
  value,
  disabled,
  onChange,
}: {
  column: Column;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const target = column.config.target_list_uuid as string | undefined;
  const display = column.config.display_column_uuid as string | undefined;
  const multiple = !!column.config.multiple;
  const { options, isLoading } = useTargetRows(target, display);
  const id = `field-${column.uuid}`;

  if (multiple) {
    return (
      <MultiSelect
        inputId={id}
        value={(value as string[]) ?? []}
        options={options}
        onChange={(e) => onChange(e.value)}
        disabled={disabled || isLoading}
        filter
        display="chip"
        placeholder={isLoading ? "Loading…" : "Select rows…"}
      />
    );
  }
  return (
    <Dropdown
      inputId={id}
      value={(value as string) ?? null}
      options={options}
      onChange={(e) => onChange(e.value)}
      disabled={disabled || isLoading}
      filter
      showClear
      placeholder={isLoading ? "Loading…" : "Select a row…"}
    />
  );
}
