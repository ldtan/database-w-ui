import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";

import { api } from "../lib/api";
import { useCurrentUser } from "../lib/auth";
import { useDebounced } from "../lib/useDebounced";
import type { ApiUser } from "../lib/types";

interface Props {
  value: string | null;
  onChange: (uuid: string | null) => void;
  /** Don't fetch until the picker is actually on screen — both callers keep it
   *  mounted behind a mode toggle or a closed dialog. */
  enabled?: boolean;
  placeholder?: string;
}

/**
 * Pick one other user, by uuid.
 *
 * The search runs server-side rather than filtering a fetched page, so a name
 * past the first fifty is still findable — the reason the dropdown's own filter
 * is wired to a query instead of to its options. The current user is never
 * offered: every use of this is handing something *to* someone, and both
 * transfer endpoints reject a self-transfer with a 400.
 */
export function UserPicker({
  value,
  onChange,
  enabled = true,
  placeholder = "Select a user…",
}: Props) {
  const me = useCurrentUser();
  const [search, setSearch] = useState("");
  const q = useDebounced(search);

  const users = useQuery({
    queryKey: ["users", "transfer-search", q],
    queryFn: () =>
      api.get<ApiUser[]>(`/auth/users/?limit=50&q=${encodeURIComponent(q.trim())}`),
    enabled,
  });
  const options = (users.data ?? [])
    .filter((u) => u.uuid !== me?.uuid)
    .map((u) => ({ label: u.username, value: u.uuid }));

  return (
    <Dropdown
      value={value}
      options={options}
      onChange={(e) => onChange(e.value)}
      placeholder={placeholder}
      filter
      // Drive the server-side search; options are already narrowed.
      filterMatchMode="contains"
      onFilter={(e) => setSearch(e.filter)}
      resetFilterOnHide
      emptyFilterMessage="No matching users"
      className="w-full"
    />
  );
}
