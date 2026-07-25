import type { Person } from "../lib/types";

/** Nothing in the model holds a picture — `Person` is names and a birth date — so
 *  the circle is initials, from the profile if there is one and from the username
 *  otherwise. It is shaped for an <img> if an avatar ever lands. */
function initials(person: Person | null, username: string): string {
  if (person) {
    return `${person.first_name[0] ?? ""}${person.last_name[0] ?? ""}`.toUpperCase();
  }
  return (username[0] ?? "?").toUpperCase();
}

interface Props {
  person: Person | null | undefined;
  username: string;
  /** Any CSS length. The drawer's default; the header asks for a smaller one so the
   *  circle sits in a row of icon buttons without setting the header's height. */
  size?: string;
}

/**
 * The signed-in user as a circle.
 *
 * One component because the drawer and the header show the same person, and a copy
 * of this is a copy of the initials rule with it — the day a real avatar arrives,
 * two circles would have to learn about it separately.
 *
 * It takes what it draws rather than fetching it: both callers already hold the user
 * and the profile, and a component that fetched would query once per place it appears.
 */
export function UserAvatar({ person, username, size = "2.75rem" }: Props) {
  return (
    <span
      className="flex items-center justify-center rounded-full shrink-0 font-semibold"
      style={{
        width: size,
        height: size,
        background: "var(--brand)",
        color: "#fff",
      }}
      aria-hidden
    >
      {initials(person ?? null, username)}
    </span>
  );
}
