import type { ReactNode } from "react";

interface Props {
  /** A PrimeIcons class — `pi pi-inbox` and friends. */
  icon: string;
  title: string;
  /** What to do about it, or why it is empty. Optional: a search with no hits
   *  needs no explaining. */
  text?: ReactNode;
  /** The way out of the empty state, when there is one the caller may take. */
  action?: ReactNode;
}

/**
 * Nothing here — said in a way that helps.
 *
 * The pages used to answer an empty result with a bare paragraph ("No workspaces
 * yet."), which states the problem and stops. An empty state is the one screen
 * where a new user is *guaranteed* to land, so it is the one that should carry
 * the next step: what this page holds, and the button that puts something in it.
 */
export function EmptyState({ icon, title, text, action }: Props) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon" aria-hidden>
        <i className={icon} />
      </span>
      <span className="empty-state-title">{title}</span>
      {text && <p className="empty-state-text">{text}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
