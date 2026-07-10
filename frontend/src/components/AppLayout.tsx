import { Link, Outlet } from "react-router-dom";
import { Button } from "primereact/button";

import { useAuth } from "../lib/auth";

export function AppLayout() {
  const { logout } = useAuth();

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <Link to="/" className="text-lg font-semibold no-underline">
          Database UI
        </Link>
        <Button label="Log out" icon="pi pi-sign-out" text onClick={logout} />
      </header>
      <main className="p-4">
        <Outlet />
      </main>
    </div>
  );
}
