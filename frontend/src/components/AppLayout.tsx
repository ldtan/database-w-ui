import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Button } from "primereact/button";

import { DESKTOP_SCREEN, useMediaQuery } from "../lib/useMediaQuery";
import { AccountMenu } from "./AccountMenu";
import { AppSidebar } from "./AppSidebar";
import { NavDrawer } from "./NavDrawer";
import { ThemeSwitcher } from "./ThemeSwitcher";

/**
 * The shell: a navigation rail on the left, and the page beside it.
 *
 * Which of the two navigations is mounted is a question about *width*, and a
 * different one from "does a data grid fit" (`SMALL_SCREEN`, 640px) — a tablet
 * is wide enough for a table and not for a table plus a 16rem rail. So the rail
 * has its own breakpoint at 1024px, and below it the same tree lives in a
 * drawer behind the header's burger.
 *
 * The header only exists below that breakpoint: on a desktop the rail already
 * carries the wordmark, the account and the theme controls, and a bar repeating
 * them would be a strip of chrome saying nothing.
 */
export function AppLayout() {
  const wide = useMediaQuery(DESKTOP_SCREEN);
  const [navOpen, setNavOpen] = useState(false);
  const { pathname } = useLocation();

  // Widening past the breakpoint unmounts the drawer but would leave it *open* —
  // so narrowing back (a rotation, a resized window) flung it across the page
  // again with nobody touching the burger.
  useEffect(() => {
    if (wide) setNavOpen(false);
  }, [wide]);

  return (
    <div className="app-shell">
      {wide && <AppSidebar />}

      <div className="app-content">
        {!wide && (
          <header className="app-header flex items-center justify-between gap-2 px-3 py-2">
            <div className="flex items-center gap-1">
              <Button
                icon="pi pi-bars"
                text
                rounded
                aria-label="Menu"
                aria-expanded={navOpen}
                onClick={() => setNavOpen(true)}
              />
              <Link to="/" className="app-brand">
                <span className="app-brand-mark" aria-hidden>
                  <i className="pi pi-database" />
                </span>
                <span className="truncate">Database w/ UI</span>
              </Link>
            </div>
            <div className="flex items-center gap-1">
              <ThemeSwitcher />
              <AccountMenu />
            </div>
          </header>
        )}

        {/* Mounted only when there is no rail: it carries the same tree, and
            fetching that twice over would be work for nobody. */}
        {!wide && (
          <NavDrawer visible={navOpen} onHide={() => setNavOpen(false)} />
        )}

        {/* Keyed on the path so each page fades in on its own rather than the
            new one inheriting the old one's finished animation. */}
        <main key={pathname} className="page-enter p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
