import { Link } from "react-router-dom";

import { AccountMenu } from "./AccountMenu";
import { NavTree } from "./NavTree";
import { ThemeSwitcher } from "./ThemeSwitcher";

/**
 * The desktop navigation rail.
 *
 * It exists because reaching a list used to be three page loads every time —
 * home, then the workspace, then the list — with nothing on screen to say what
 * else there was. A rail that is simply *there* turns that into one click and,
 * more to the point, keeps the shape of your data visible while you work in it,
 * which is what every tool of this kind does and this one did only on a phone.
 *
 * Below `DESKTOP_SCREEN` it is not rendered at all: `NavDrawer` carries the same
 * `NavTree` behind the header's burger. Two widths, one navigation.
 */
export function AppSidebar() {
  return (
    <aside className="app-sidebar">
      <div className="px-3 py-3">
        <Link to="/" className="app-brand px-1">
          <span className="app-brand-mark" aria-hidden>
            <i className="pi pi-database" />
          </span>
          <span className="truncate">Database</span>
        </Link>
      </div>

      {/* Only this band scrolls. A long list of workspaces must not push the
          account off the bottom of the world, which is exactly what one
          scrolling column does. */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        <NavTree enabled />
      </div>

      <div
        className="px-3 py-2 flex items-center gap-1"
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
