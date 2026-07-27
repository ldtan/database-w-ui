import type { ReactNode } from "react";

import { Breadcrumbs, type Crumb } from "./Breadcrumbs";

interface Props {
  /** Omitted on the home dashboard, which is the root of every trail. */
  crumbs?: Crumb[];
  title: string;
  /** One line on what the page is for, when the title does not already say it. */
  subtitle?: ReactNode;
  /** The page's own controls — a primary button, usually with an overflow. */
  actions?: ReactNode;
}

/**
 * The top of a page: where you are, what this is, and what you can do here.
 *
 * Every page had grown its own arrangement of these three — a breadcrumb, then an
 * `<h1>` in a flex row with a button or two — at slightly different sizes and
 * spacings. One component means a page cannot be subtly the wrong shape, and the
 * actions cluster wraps under the title on a narrow window instead of squeezing
 * it.
 */
export function PageHeader({ crumbs, title, subtitle, actions }: Props) {
  return (
    <header className="flex flex-col gap-2">
      {crumbs && <Breadcrumbs items={crumbs} />}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1 min-w-0">
          <h1 className="text-2xl font-semibold truncate">{title}</h1>
          {subtitle && (
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              {subtitle}
            </p>
          )}
        </div>
        {actions && (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        )}
      </div>
    </header>
  );
}
