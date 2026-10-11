/**
 * Moving one entry of an ordered table to another position.
 *
 * Extracted from the two components that reorder things — a view's columns and a
 * form's fields — because it was written inline in both, and because a closure
 * inside a component is unreachable from a test: vitest runs in `node` here with
 * `include: ["src/**\/*.test.ts"]`, deliberately (see vite.config.ts), so the only
 * testable unit is a pure function in a module of its own.
 *
 * A **splice**, not a swap. The two agree while the distance is 1, which is why the
 * up/down buttons behaved identically before and after this — but a drag moves an
 * entry several places at once, and swapping there would fling whatever sat at the
 * destination back to where the dragged row came from.
 */
export function moveTo<T>(items: readonly T[], from: number, to: number): T[] {
  // Out of range is a no-op rather than an error: the callers are a drag that can
  // end outside the table and a button at the end of one, and neither has anything
  // useful to do with a throw.
  if (
    from === to ||
    from < 0 ||
    to < 0 ||
    from >= items.length ||
    to >= items.length
  ) {
    return [...items];
  }
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}
