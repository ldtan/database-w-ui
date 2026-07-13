import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { api, ApiError } from "./api";
import type { Person } from "./types";

/**
 * The signed-in user's profile, or null when they have not made one.
 *
 * A 404 here is a normal state, not an error — an account can exist without a
 * person record — which is a rule that has to be applied identically wherever the
 * profile is read, and was being copied instead (the profile page and the nav
 * drawer each had their own version of this query, its key and its try/catch).
 */
export function useMyPerson(): UseQueryResult<Person | null> {
  return useQuery<Person | null>({
    queryKey: ["person", "me"],
    retry: false,
    queryFn: async () => {
      try {
        return await api.get<Person>("/people/me");
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }
    },
  });
}

/**
 * A person's name, as the UI writes it: honorifics included.
 *
 * One function, because two of them means the same person reads "Dr Jane Q Smith
 * Jr" in one place and "Jane Q Smith" in another, and nobody decided that — it is
 * just where the copies drifted.
 */
export function fullName(person: Person | null | undefined): string {
  if (!person) return "";
  return [
    person.name_prefix,
    person.first_name,
    person.middle_name,
    person.last_name,
    person.name_suffix,
  ]
    .filter(Boolean)
    .join(" ");
}
