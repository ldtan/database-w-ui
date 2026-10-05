import type { ReactNode } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Button } from "primereact/button";

import { ThemeSwitcher } from "./ThemeSwitcher";
import { ErrorBoundary } from "./ErrorBoundary";

/**
 * The shell for the pages a signed-out visitor can reach: the home page and, with
 * it, the guide. Only mounted when `HOME_ENABLED` — see `App.tsx`.
 *
 * It is deliberately thin, and the list of what it must *not* touch is the design:
 * no `OnboardingProvider`, no `AppSidebar`, no `useCurrentUser`, no `Breadcrumbs`.
 * Every one of those reads an account, and there is no account here. A visitor gets
 * a bar with the wordmark, the theme controls and a way in — nothing that implies a
 * session they do not have, and nothing that can fail because a request 401'd.
 *
 * `ThemeSwitcher` is the one app control that belongs here: it reads `ThemeProvider`,
 * which `main.tsx` mounts above the router, and writes `localStorage`. So a stranger
 * can read the guide in dark mode, and `themeSync` later reconciles that choice with
 * their account when they sign in — the preference survives the sign-in rather than
 * being asked for twice.
 *
 * The bar reuses `.app-header`, not a copy of it: a visitor who signs in should not
 * watch the header change shape under them. What differs is that this one is there at
 * every width — `AppLayout` only renders a header below the rail breakpoint — and
 * that costs one scoped CSS rule, noted on `.guide-section` in `themes.css`.
 */
export function PublicLayout() {
  const { pathname } = useLocation();

  return (
    <div className="public-shell">
      <header className="app-header flex items-center px-3 sm:px-4 py-2">
        {/* The bar spans the window; its contents line up with the page's column,
            so the wordmark sits over the hero's text rather than in the corner. */}
        <div className="public-bar flex items-center gap-2 sm:gap-3">
          {/* `min-w-0` so the wordmark truncates on a narrow screen instead of
              pushing the buttons off the edge — the same reason AppLayout's does. */}
          <Link to="/" className="app-brand min-w-0">
            <span className="app-brand-mark" aria-hidden>
              <i className="pi pi-database" />
            </span>
            <span className="truncate">Database w/ UI</span>
          </Link>

          <div className="ml-auto flex items-center gap-1 sm:gap-2">
            <ThemeSwitcher />
            {/* Hidden on the guide itself, where it would point at the page you are
                already reading, and below 640px, where the two buttons and the
                theme controls are more than the bar can hold. */}
            {pathname !== "/guide" && (
              <Link to="/guide" className="no-underline hidden sm:block">
                <Button label="Guide" icon="pi pi-book" text />
              </Link>
            )}
            <Link to="/login" className="no-underline">
              <Button label="Log in" icon="pi pi-sign-in" />
            </Link>
          </div>
        </div>
      </header>

      {/* No padding: the home page is full-bleed and owns its own, and a document
          wraps itself in `PublicDocument` below. Keyed on the path for the same
          reason AppLayout's is — each page fades in on its own, and a page that
          threw is reset by navigating away from it.

          The boundary is around the page rather than the app, so a failure leaves
          the bar standing and `Log in` still clickable. */}
      <main key={pathname} className="page-enter public-main">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>

      <footer className="public-footer">
        <div className="public-bar flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span style={{ color: "var(--text-muted)" }}>
            Database w/ UI — a self-hostable app for building databases through a
            screen.
          </span>
          <span className="ml-auto flex items-center gap-4">
            <Link to="/guide" style={{ color: "var(--brand)" }}>
              Guide
            </Link>
            <a
              href="https://github.com/ldtan/database-w-ui"
              style={{ color: "var(--brand)" }}
              // An outbound link in a footer, so the usual pair: no referrer, and
              // no window.opener handed to a page we do not control.
              target="_blank"
              rel="noreferrer"
            >
              Source
            </a>
            <Link to="/login" style={{ color: "var(--brand)" }}>
              Log in
            </Link>
          </span>
        </div>
      </footer>
    </div>
  );
}

/**
 * A centred, padded column for a public page that is a *document* — which is to say,
 * the guide. The home page is full-bleed and brings its own padding, so this is not
 * in the layout itself.
 *
 * The width only has to clear the guide's own `maxWidth: 820` plus the padding on
 * either side; the guide still caps itself, so this centres it rather than setting it.
 */
export function PublicDocument({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto w-full p-4 sm:p-6" style={{ maxWidth: 868 }}>
      {children}
    </div>
  );
}
