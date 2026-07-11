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
        <div className="flex items-center gap-2">
          <ThemeSwitcher />
          <Button label="Log out" icon="pi pi-sign-out" text onClick={logout} />
        </div>
      </header>
      <main className="p-4">
        <Outlet />
      </main>
    </div>
  );
}
