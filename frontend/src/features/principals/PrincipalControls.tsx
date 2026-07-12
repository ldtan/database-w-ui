import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { MultiSelect } from "primereact/multiselect";

import { api } from "../../lib/api";
import type { ApiUser, ApiUserGroup, Column, ColumnType } from "../../lib/types";

type PrincipalType = Extract<ColumnType, "user" | "group">;

interface Principals {
  isLoading: boolean;
  options: { label: string; value: string }[];
  labelFor: (uuid: string) => string;
}

// Resolve the uuids a user/group column stores into names to show and pick.
//
// Users are labelled by **username, never the connected person's name**: person
// records are private (owner or superuser only), while /auth/users is readable by
// any authenticated user so the Share dialog can list principals. Rendering
// "Grace Hopper" in a cell would leak personal data to everyone who can view the
// list.
function usePrincipals(kind: PrincipalType): Principals {
  const path = kind === "user" ? "/auth/users/" : "/auth/groups/";
  const query = useQuery({
    // Shared cache key: several cells and the picker all read the same page.
    queryKey: ["principals", kind],
    queryFn: () => api.get<(ApiUser | ApiUserGroup)[]>(`${path}?limit=1000`),
  });

  const rows = query.data;
  const { byId, options } = useMemo(() => {
    const map = new Map<string, string>();
    const opts: { label: string; value: string }[] = [];
    for (const row of rows ?? []) {
      const label =
        kind === "user" ? (row as ApiUser).username : (row as ApiUserGroup).name;
      map.set(row.uuid, label);
      opts.push({ label, value: row.uuid });
    }
    return { byId: map, options: opts };
  }, [rows, kind]);

  return {
    isLoading: query.isLoading,
    options,
    // A uuid with no match is one whose account/group is gone. Deletion scrubs
    // these, so it should not happen — but say so rather than print a raw uuid.
    labelFor: (uuid) => byId.get(uuid) ?? `unknown ${kind}`,
  };
}

export function PrincipalCell({
  column,
  value,
}: {
  column: Column;
  value: unknown;
}) {
  const { labelFor } = usePrincipals(column.type as PrincipalType);
  const uuids = Array.isArray(value)
    ? (value as string[])
    : value
      ? [String(value)]
      : [];
  if (uuids.length === 0) return <span className="text-gray-300">—</span>;
  return <span>{uuids.map(labelFor).join(", ")}</span>;
}

export function PrincipalField({
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
  const kind = column.type as PrincipalType;
  const multiple = !!column.config.multiple;
  const { options, isLoading } = usePrincipals(kind);
  const id = `field-${column.uuid}`;
  const noun = kind === "user" ? "user" : "group";

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
        placeholder={isLoading ? "Loading…" : `Select ${noun}s…`}
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
      placeholder={isLoading ? "Loading…" : `Select a ${noun}…`}
    />
  );
}

/** The value picker for a filter condition: same options, plain single-select. */
export function PrincipalFilterValue({
  column,
  value,
  onChange,
}: {
  column: Column;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const { options, isLoading } = usePrincipals(column.type as PrincipalType);
  return (
    <Dropdown
      value={(value as string) ?? null}
      options={options}
      onChange={(e) => onChange(e.value)}
      placeholder={isLoading ? "Loading…" : "Value"}
      filter
      style={{ width: 120 }}
    />
  );
}
