import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "./lib/auth";
import { ThemeSync } from "./lib/themeSync";
import { TimezoneSync } from "./lib/timezoneSync";
import { AppLayout } from "./components/AppLayout";
import { LoginPage } from "./features/auth/LoginPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { WorkspacesPage } from "./features/workspaces/WorkspacesPage";
import { ListsPage } from "./features/lists/ListsPage";
import { ListDetailPage } from "./features/lists/ListDetailPage";
import { PeoplePage } from "./features/people/PeoplePage";
import { ProfilePage } from "./features/profile/ProfilePage";
import { GuidePage } from "./features/guide/GuidePage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <>
      {/* Both render nothing; inside AuthProvider so they can reach the account,
          outside <Routes> so a navigation cannot unmount them mid-save. ThemeSync
          reconciles this device's theme with the account's; TimezoneSync tells the
          account which zone this browser is in, so the server can stamp a `today`
          default on the user's calendar. */}
      <ThemeSync />
      <TimezoneSync />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="guide" element={<GuidePage />} />
          <Route path="people" element={<PeoplePage />} />
          <Route path="profile" element={<ProfilePage />} />
          <Route path="workspaces" element={<WorkspacesPage />} />
          <Route path="workspaces/:workspaceUuid" element={<ListsPage />} />
          <Route path="lists/:listUuid" element={<ListDetailPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
