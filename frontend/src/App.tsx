import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "./lib/auth";
import { ThemeSync } from "./lib/themeSync";
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
      {/* Renders nothing; inside AuthProvider so it can reconcile the device's
          theme with the account's. Outside <Routes> so a navigation cannot
          unmount it mid-save. */}
      <ThemeSync />
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
