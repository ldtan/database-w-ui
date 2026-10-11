/**
 * Placeholders in the shape of what is coming.
 *
 * A page that says "Loading…" and then replaces it with a grid of cards moves
 * everything on screen at the moment the data lands — which is the moment
 * somebody is most likely to be reaching for it. A skeleton occupies the space
 * the answer will occupy, so nothing jumps and the wait reads as progress rather
 * than as an empty page.
 *
 * Purely decorative, so all of it is hidden from assistive technology: a screen
 * reader gets the content when there is content, not a description of grey bars.
 */

interface BarProps {
  /** Any CSS width — a percentage makes a run of bars look like text. */
  width?: string;
  height?: string;
  className?: string;
}

export function SkeletonBar({
  width = "100%",
  height = "0.875rem",
  className = "",
}: BarProps) {
  return (
    <div className={`skeleton ${className}`} style={{ width, height }} />
  );
}

/** A run of card-shaped placeholders, for the grids on the workspace and table
 *  pages. */
export function SkeletonCards({ count = 6 }: { count?: number }) {
  return (
    <div className="card-grid" aria-hidden>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="surface-card p-4 flex flex-col gap-2">
          <SkeletonBar width="60%" height="1rem" />
          <SkeletonBar width="40%" />
        </div>
      ))}
    </div>
  );
}

/** A run of row-shaped placeholders, for the stacked tables (people, groups). */
export function SkeletonRows({ count = 4 }: { count?: number }) {
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="surface-card p-4 flex flex-col gap-2">
          <SkeletonBar width="35%" height="1rem" />
          <SkeletonBar width="55%" />
        </div>
      ))}
    </div>
  );
}
