import { BreadCrumb } from "primereact/breadcrumb";
import type { MenuItem } from "primereact/menuitem";
import { Link } from "react-router-dom";

export interface Crumb {
  label: string;
  /** Omit on the current page — it renders as plain text rather than a link. */
  to?: string;
}

/**
 * The trail always roots at the home dashboard (the home icon). Crumbs render
 * through react-router Links rather than MenuItem.url, which would do a full page
 * reload.
 */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  const home: MenuItem = {
    template: () => (
      <Link to="/" className="p-menuitem-link" aria-label="Home">
        <span className="pi pi-home" />
      </Link>
    ),
  };

  // Every crumb gets an explicit template: without one PrimeReact renders the item
  // as <a href="#">, so the current page would look and behave like a link.
  const model: MenuItem[] = items.map((crumb) => ({
    label: crumb.label,
    template: crumb.to
      ? () => (
          <Link to={crumb.to!} className="p-menuitem-link">
            {crumb.label}
          </Link>
        )
      : () => <span className="p-menuitem-text">{crumb.label}</span>,
  }));

  return <BreadCrumb home={home} model={model} className="mb-1" />;
}
