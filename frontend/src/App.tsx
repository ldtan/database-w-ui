import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { Suspense, lazy, type ReactNode } from "react";

import { useAuth } from "./lib/auth";
import { HOME_ENABLED } from "./lib/config";
import { ThemeSync } from "./lib/themeSync";
import { TimezoneSync } from "./lib/timezoneSync";
import { AppLayout } from "./components/AppLayout";
import { PublicDocument, PublicLayout } from "./components/PublicLayout";
import { LoginPage } from "./features/auth/LoginPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { WorkspacesPage } from "./features/workspaces/WorkspacesPage";
import { TablesPage } from "./features/tables/TablesPage";
import { TableDetailPage } from "./features/tables/TableDetailPage";
import { PeoplePage } from "./features/people/PeoplePage";
import { ProfilePage } from "./features/profile/ProfilePage";
import { GuidePage } from "./features/guide/GuidePage";

/** The public home page, in its own chunk.
 *
 *  Split for a different reason from the QR dialogs in `TableDetailPage` (there, a
 *  heavy decoder most sessions never need): this page is small, but a deployment with
 *  `HOME_ENABLED` off never shows it at all, and on one that does, the people using
 *  the app every day are not the ones it is for. Either way nobody who will not read
 *  it downloads it.
 *
 *  What that does *not* do is hide it. Vite still emits the chunk — the flag reaches
 *  the condition below through a function call across a module boundary, so it cannot
 *  be folded into a constant and the branch pruned. Nothing on the page is private,
 *  so bytes on the wire is the whole of the win. */
const HomePage = lazy(() =>
  import("./features/home/HomePage").then((m) => ({ default: m.HomePage })),
);

function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

/** `/lists/<uuid>` was the only deep link this app ever had, so it is the one URL
 *  people have bookmarked and pasted to each other. The rename moved it to
 *  `/tables/<uuid>`; this keeps the old spelling working forever rather than
 *  answering a saved link with the catch-all redirect to `/`, which would look like
 *  the table had been deleted.
 *
 *  A component rather than a bare `<Navigate>` because the uuid has to be carried
 *  across, and `to` takes no route params. `replace` so the dead URL does not sit in
 *  history waiting for the back button. */
function LegacyTableRedirect() {
  const { tableUuid } = useParams();
  return <Navigate to={`/tables/${tableUuid}`} replace />;
}

export function App() {
  const { isAuthenticated } = useAuth();

  /** Whether to serve the signed-out pages at all.
   *
   *  Two conditions, and the second is what keeps the change this small: because the
   *  public routes are not declared for somebody who holds a session, the dashboard
   *  does not have to move off `/`. A visitor gets the home page there, a signed-in
   *  user gets the dashboard there, and no link, redirect or breadcrumb anywhere else
   *  in the app had to learn a new path. */
  const showPublic = HOME_ENABLED && !isAuthenticated;

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

        {/* Declared before the guarded layout, and that order is load-bearing: with
            the flag on, `/` and `/guide` each match here *and* below with the same
            specificity, and React Router breaks that tie by declaration order. If it
            ever gets reversed the symptom is immediate rather than subtle — `/` sends
            a visitor to the sign-in form, which is the whole thing this undoes. */}
        {showPublic && (
          <Route path="/" element={<PublicLayout />}>
            <Route
              index
              element={
                <Suspense fallback={<PublicPending />}>
                  <HomePage />
                </Suspense>
              }
            />
            {/* The same guide the app shows, minus the breadcrumb and the tour — it
                fetches nothing, so there is one document rather than two. */}
            <Route
              path="guide"
              element={
                <PublicDocument>
                  <GuidePage standalone />
                </PublicDocument>
              }
            />
          </Route>
        )}

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
          <Route path="workspaces/:workspaceUuid" element={<TablesPage />} />
          <Route path="tables/:tableUuid" element={<TableDetailPage />} />
          <Route path="lists/:tableUuid" element={<LegacyTableRedirect />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}

/** What stands in while the home page's chunk arrives.
 *
 *  Deliberately almost nothing: it is one request against a CDN, and a spinner that
 *  appears and leaves inside a frame is more distracting than a blank band. The
 *  height keeps the bar above from sitting on the footer while it loads. */
function PublicPending() {
  return <div style={{ minHeight: "60vh" }} aria-busy="true" />;
}
