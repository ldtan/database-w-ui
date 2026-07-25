import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { Button } from "primereact/button";

import { SMALL_SCREEN, useMediaQuery } from "../lib/useMediaQuery";
import { AccountMenu } from "./AccountMenu";
import { NavDrawer } from "./NavDrawer";
import { ThemeSwitcher } from "./ThemeSwitcher";

export function AppLayout() {
  // The same 640px the records switch to cards at — one breakpoint, so the app
  // does not develop two ideas of what a small screen is.
  const small = useMediaQuery(SMALL_SCREEN);
  const [navOpen, setNavOpen] = useState(false);

  // Widening past the breakpoint unmounts the drawer but would leave it *open* —
  // so narrowing back (a rotation, a resized window) flung it across the page
  // again with nobody touching the burger.
  useEffect(() => {
    if (!small) setNavOpen(false);
  }, [small]);

  return (
    <div className="min-h-screen">
      <header className="app-header flex items-center justify-between px-4 py-3">
        <Link to="/" className="text-lg font-semibold no-underline">
          Database w/ UI
        </Link>
        <div className="flex items-center gap-1 sm:gap-2">
          {/* The theme controls stay out of the drawer on every screen: they are two
              icons wide, and a light/dark toggle you have to open a panel for is a
              toggle you stop using. The account avatar does not — on a phone the
              drawer already carries the account band, and a second way to reach it
              would cost a third of the header to say the same thing twice. */}
          <ThemeSwitcher />
          {small ? (
            <Button
              icon="pi pi-bars"
              text
              rounded
              aria-label="Menu"
              aria-expanded={navOpen}
              onClick={() => setNavOpen(true)}
            />
          ) : (
            <>
              <Link to="/workspaces" className="no-underline">
                <Button label="Workspaces" icon="pi pi-th-large" text />
              </Link>
              <Link to="/guide" className="no-underline">
                <Button label="Guide" icon="pi pi-book" text />
              </Link>
              <Link to="/people" className="no-underline">
                <Button label="People" icon="pi pi-users" text />
              </Link>
              <AccountMenu />
            </>
          )}
        </div>
      </header>

      {/* Mounted only on a small screen: it carries the workspace/list tree, and
          fetching that on a desktop — where the same navigation is a click away in
          the page itself — would be work for nobody. */}
      {small && (
        <NavDrawer visible={navOpen} onHide={() => setNavOpen(false)} />
      )}

      <main className="p-3 sm:p-4">
        <Outlet />
      </main>
    </div>
  );
}
