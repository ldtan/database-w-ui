import { useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { Button } from "primereact/button";

import { useAuth } from "../lib/auth";
import { SMALL_SCREEN, useMediaQuery } from "../lib/useMediaQuery";
import { NavDrawer } from "./NavDrawer";
import { ThemeSwitcher } from "./ThemeSwitcher";

export function AppLayout() {
  const { logout } = useAuth();
  // The same 640px the records switch to cards at — one breakpoint, so the app
  // does not develop two ideas of what a small screen is.
  const small = useMediaQuery(SMALL_SCREEN);
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="min-h-screen">
      <header className="app-header flex items-center justify-between px-4 py-3">
        <Link to="/" className="text-lg font-semibold no-underline">
          Database UI
        </Link>
        <div className="flex items-center gap-1 sm:gap-2">
          {/* The theme controls stay out of the drawer on every screen: they are two
              icons wide, and a light/dark toggle you have to open a panel for is a
              toggle you stop using. */}
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
              <Link to="/people" className="no-underline">
                <Button label="People" icon="pi pi-users" text />
              </Link>
              <Link to="/profile" className="no-underline">
                <Button label="Profile" icon="pi pi-user" text />
              </Link>
              <Button
                label="Log out"
                icon="pi pi-sign-out"
                text
                onClick={logout}
              />
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
