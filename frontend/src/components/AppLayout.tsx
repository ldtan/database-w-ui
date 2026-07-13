import { Link, Outlet } from "react-router-dom";
import { Button } from "primereact/button";

import { useAuth } from "../lib/auth";
import { ThemeSwitcher } from "./ThemeSwitcher";

export function AppLayout() {
  const { logout } = useAuth();

  return (
    <div className="min-h-screen">
      <header className="app-header flex items-center justify-between px-4 py-3">
        <Link to="/" className="text-lg font-semibold no-underline">
          Database UI
        </Link>
        {/* The labels are hidden below 640px (index.css) — hence the aria-labels,
            which is all that is left of them to a screen reader. */}
        <div className="flex items-center gap-1 sm:gap-2">
          <Link to="/people" className="no-underline">
            <Button label="People" icon="pi pi-users" text aria-label="People" />
          </Link>
          <Link to="/profile" className="no-underline">
            <Button label="Profile" icon="pi pi-user" text aria-label="Profile" />
          </Link>
          <ThemeSwitcher />
          <Button
            label="Log out"
            icon="pi pi-sign-out"
            text
            aria-label="Log out"
            onClick={logout}
          />
        </div>
      </header>
      <main className="p-3 sm:p-4">
        <Outlet />
      </main>
    </div>
  );
}
