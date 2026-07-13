import { useRef } from "react";
import { Link, Outlet, useNavigate } from "react-router-dom";
import { Button } from "primereact/button";
import { Menu } from "primereact/menu";
import type { MenuItem } from "primereact/menuitem";

import { useAuth } from "../lib/auth";
import { SMALL_SCREEN, useMediaQuery } from "../lib/useMediaQuery";
import { ThemeSwitcher } from "./ThemeSwitcher";

export function AppLayout() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  // The same 640px the records switch to cards at — one breakpoint, so the app
  // does not have two ideas of what a small screen is.
  const small = useMediaQuery(SMALL_SCREEN);
  const menu = useRef<Menu>(null);

  // Navigation by `command` rather than `url`: the latter is a plain href, which
  // reloads the whole app instead of routing.
  const items: MenuItem[] = [
    { label: "People", icon: "pi pi-users", command: () => navigate("/people") },
    { label: "Profile", icon: "pi pi-user", command: () => navigate("/profile") },
    { separator: true },
    { label: "Log out", icon: "pi pi-sign-out", command: logout },
  ];

  return (
    <div className="min-h-screen">
      <header className="app-header flex items-center justify-between px-4 py-3">
        <Link to="/" className="text-lg font-semibold no-underline">
          Database UI
        </Link>
        <div className="flex items-center gap-1 sm:gap-2">
          {/* The theme controls stay out of the menu on every screen: they are two
              icons wide, and a light/dark toggle you have to open a menu for is a
              toggle you stop using. */}
          <ThemeSwitcher />
          {small ? (
            <>
              <Button
                icon="pi pi-bars"
                text
                rounded
                aria-label="Menu"
                aria-haspopup
                aria-controls="app-menu"
                onClick={(e) => menu.current?.toggle(e)}
              />
              <Menu id="app-menu" ref={menu} model={items} popup />
            </>
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
      <main className="p-3 sm:p-4">
        <Outlet />
      </main>
    </div>
  );
}
