import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Button } from "primereact/button";
import { Sidebar } from "primereact/sidebar";

import { DESKTOP_SCREEN, useMediaQuery } from "../lib/useMediaQuery";
import { HelpWidget } from "../features/onboarding/HelpWidget";
import { OnboardingProvider } from "../features/onboarding/OnboardingProvider";
import { AppSidebar } from "./AppSidebar";
import { ErrorBoundary } from "./ErrorBoundary";

/**
 * The shell: a navigation rail on the left, and the page beside it.
 *
 * Where the rail *sits* is a question about width, and a different one from "does
 * a data grid fit" (`SMALL_SCREEN`, 640px) — a tablet is wide enough for a table
 * and not for a table plus a 17rem rail. So the rail has its own breakpoint at
 * 1024px, and below it the same `AppSidebar` is mounted inside an overlay behind
 * the header's burger. One rail, two placements: there is no second navigation
 * component to keep in step, which is what the old `NavDrawer` had become.
 *
 * The header only exists below that breakpoint, and holds only the burger and the
 * wordmark. Its account and theme controls moved into the rail's own footer when
 * the rail started appearing here — a drawer only 20rem wide leaves the right of
 * the header showing beside it, so a copy of each control would have sat visible
 * next to the original.
 *
 * The whole of it sits inside `OnboardingProvider`, which is what makes the help
 * button in the corner and the guided tours available on every page — and it is
 * the shell they are mostly about, so this is where they belong.
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
    <OnboardingProvider>
      <div className="app-shell">
        {wide && <AppSidebar />}

        <div className="app-content">
          {!wide && (
            <header className="app-header flex items-center gap-2 px-3 py-2">
              {/* `min-w-0` so the wordmark is what gives way on a narrow screen —
                  without it the flex row refuses to shrink the link and pushes the
                  burger off the edge instead of truncating the text. */}
              <Link to="/" className="app-brand min-w-0">
                <span className="app-brand-mark" aria-hidden>
                  <i className="pi pi-database" />
                </span>
                <span className="truncate">Database w/ UI</span>
              </Link>
              <Button
                data-tour="nav-toggle"
                icon="pi pi-bars"
                text
                rounded
                aria-label="Menu"
                aria-expanded={navOpen}
                onClick={() => setNavOpen(true)}
                className="ml-auto flex-shrink-0"
              />
            </header>
          )}

          {/* The rail again, as an overlay. Mounted only when there is no rail
              beside the page: mounting both would fetch the same tree twice. */}
          {!wide && (
            <Sidebar
              className="nav-drawer"
              visible={navOpen}
              onHide={() => setNavOpen(false)}
              position="left"
              style={{ width: "min(17.25rem, 85vw)" }}
            >
              <AppSidebar
                enabled={navOpen}
                onNavigate={() => setNavOpen(false)}
              />
            </Sidebar>
          )}

          {/* Keyed on the path so each page fades in on its own rather than the
              new one inheriting the old one's finished animation. The key does a
              second job now that a boundary is inside: a page that threw is reset
              by navigating away from it, because that remounts the whole of this.

              The boundary is *here*, around the page rather than around the app,
              so a page that fails leaves the rail, the header and the help button
              standing — there is somewhere to click next. Outside it, a single
              render error still took the navigation down with the page. */}
          <main key={pathname} className="page-enter p-4 sm:p-6">
            <ErrorBoundary>
              <Outlet />
            </ErrorBoundary>
          </main>
        </div>
      </div>

      <HelpWidget />
    </OnboardingProvider>
  );
}
