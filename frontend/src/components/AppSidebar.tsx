import { Link } from "react-router-dom";

import { AccountMenu } from "./AccountMenu";
import { NavTree } from "./NavTree";
import { ThemeSwitcher } from "./ThemeSwitcher";

interface Props {
  /**
   * False while the mobile drawer is shut. The tree fetches nothing until this
   * turns true, so a panel nobody has opened costs no requests — and a workspace's
   * lists still wait for that workspace to be expanded.
   */
  enabled?: boolean;
  /**
   * Called after any navigation from inside the rail. The drawer passes its own
   * close here; on desktop there is nothing to close and it stays undefined.
   */
  onNavigate?: () => void;
}

/**
 * The navigation rail — the whole of it, at both widths.
 *
 * It exists because reaching a list used to be three page loads every time —
 * home, then the workspace, then the list — with nothing on screen to say what
 * else there was. A rail that is simply *there* turns that into one click and,
 * more to the point, keeps the shape of your data visible while you work in it.
 *
 * Below `DESKTOP_SCREEN` the same component is mounted inside a `<Sidebar>`
 * overlay behind the header's burger instead of beside the page. There used to be
 * a second component for that, carrying the same `NavTree` inside a different
 * shell — which meant anything added to the rail's own bands (this account footer,
 * and whatever comes next) silently did not exist on a phone. One component
 * cannot drift from itself.
 *
 * The overlay is a real `<Sidebar>` rather than this element hidden with CSS: focus
 * trapping, escape-to-close, scroll lock and the backdrop come with it, and a panel
 * that merely looks the same has none of them.
 */
export function AppSidebar({ enabled = true, onNavigate }: Props) {
  return (
    <aside className="app-sidebar" data-tour="nav-rail">
      <div className="px-3 py-3">
        <Link to="/" className="app-brand px-1" onClick={onNavigate}>
          <span className="app-brand-mark" aria-hidden>
            <i className="pi pi-database" />
          </span>
          <span className="truncate">Database w/ UI</span>
        </Link>
      </div>

      {/* Only this band scrolls. A long list of workspaces must not push the
          account off the bottom of the world, which is exactly what one
          scrolling column does. */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        <NavTree enabled={enabled} onNavigate={onNavigate} />
      </div>

      {/* Both things you do with an account, behind the one control that also
          answers "whose account is this?". The drawer used to spell the same two
          out as a profile band and a Log out row; the menu behind this row carries
          the identical three lines and both actions, so nothing was lost by
          keeping one of them. */}
      <div
        className="px-3 py-2 flex items-center gap-1"
        data-tour="nav-account"
        style={{ borderTop: "var(--border-width) solid var(--border)" }}
      >
        <div className="flex-1 min-w-0">
          <AccountMenu variant="row" />
        </div>
        <ThemeSwitcher />
      </div>
    </aside>
  );
}
