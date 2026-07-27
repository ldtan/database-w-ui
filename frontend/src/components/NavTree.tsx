import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import type { ListRead, Workspace } from "../lib/types";

interface NavLinkProps {
  to: string;
  label: string;
  icon?: string;
  active: boolean;
  indent?: boolean;
  onNavigate?: () => void;
}

/** A row in the navigation: a link that shows where you already are rather than
 *  offering to take you there again, and closes the drawer behind it when it is
 *  the drawer it is sitting in. */
export function NavLink({
  to,
  label,
  icon,
  active,
  indent,
  onNavigate,
}: NavLinkProps) {
  return (
    <Link
      to={to}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={`nav-item${active ? " is-active" : ""}`}
      style={indent ? { marginLeft: "1.5rem" } : undefined}
    >
      {icon && <i className={icon} />}
      <span className="truncate">{label}</span>
    </Link>
  );
}

/** Three grey bars where rows are about to be. A tree that pops into existence
 *  under the cursor is a tree you mis-click; one that reserves its space does
 *  not move when it arrives. */
function NavSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-1 px-2 py-1" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: "1.35rem" }} />
      ))}
    </div>
  );
}

/** The lists of one workspace, fetched only once it is expanded — a tree that
 *  loaded every list of every workspace on mount would spend a phone's network
 *  on branches nobody looked at. The query key is the one ListsPage uses, so
 *  expanding a workspace you have already visited costs nothing. */
function WorkspaceLists({
  workspaceUuid,
  activePath,
  onNavigate,
}: {
  workspaceUuid: string;
  activePath: string;
  onNavigate?: () => void;
}) {
  const lists = useQuery({
    queryKey: ["lists", workspaceUuid],
    queryFn: () =>
      api.get<ListRead[]>(`/lists/?workspace_uuid=${workspaceUuid}`),
  });

  if (lists.isLoading) return <NavSkeleton rows={2} />;
  if (!lists.data?.length) {
    return (
      <span
        className="px-2 py-1 text-sm"
        style={{ color: "var(--text-muted)", marginLeft: "1.5rem" }}
      >
        No lists yet
      </span>
    );
  }
  return (
    <>
      {lists.data.map((list) => (
        <NavLink
          key={list.uuid}
          to={`/lists/${list.uuid}`}
          label={list.display_name}
          icon="pi pi-table"
          indent
          active={activePath === `/lists/${list.uuid}`}
          onNavigate={onNavigate}
        />
      ))}
    </>
  );
}

interface Props {
  /** False while the tree is off-screen (a closed drawer), so nothing is fetched
   *  for navigation nobody has asked to see. */
  enabled: boolean;
  /** The drawer passes its close; the sidebar, which does not go away, passes
   *  nothing. */
  onNavigate?: () => void;
}

/**
 * Everywhere you can go: the app's sections, your workspaces and their lists,
 * and the lists shared with you directly.
 *
 * One component, rendered in two places — the desktop sidebar and the mobile
 * drawer — because they are the same navigation at two widths. Two copies would
 * be two ideas of what the app contains, and they would drift the first time a
 * section was added to one of them.
 *
 * The workspace whose page (or whose list) you are on expands itself, so
 * arriving anywhere by any route leaves the tree showing where you are.
 */
export function NavTree({ enabled, onNavigate }: Props) {
  const { pathname } = useLocation();
  const [expanded, setExpanded] = useState<string[]>([]);

  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Workspace[]>("/workspaces/"),
    enabled,
  });
  // Lists shared with you directly: their workspace is not yours to see, so they
  // hang off no branch of the tree above and would otherwise be unreachable.
  const shared = useQuery({
    queryKey: ["lists", "shared"],
    queryFn: () => api.get<ListRead[]>("/lists/?shared=true"),
    enabled,
  });

  // Landing on a workspace opens it. Additive rather than exclusive: closing a
  // branch by hand must not be undone by the next render, and two open branches
  // is a thing people do.
  const workspaceMatch = pathname.match(/^\/workspaces\/([^/]+)/);
  const activeWorkspace = workspaceMatch?.[1];
  useEffect(() => {
    if (!activeWorkspace) return;
    setExpanded((prev) =>
      prev.includes(activeWorkspace) ? prev : [...prev, activeWorkspace],
    );
  }, [activeWorkspace]);

  function toggle(uuid: string) {
    setExpanded((prev) =>
      prev.includes(uuid) ? prev.filter((u) => u !== uuid) : [...prev, uuid],
    );
  }

  return (
    <nav className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <NavLink
          to="/"
          label="Home"
          icon="pi pi-home"
          active={pathname === "/"}
          onNavigate={onNavigate}
        />
        <NavLink
          to="/workspaces"
          label="Workspaces"
          icon="pi pi-th-large"
          active={pathname === "/workspaces"}
          onNavigate={onNavigate}
        />
        <NavLink
          to="/people"
          label="People"
          icon="pi pi-users"
          active={pathname === "/people"}
          onNavigate={onNavigate}
        />
        <NavLink
          to="/guide"
          label="Guide"
          icon="pi pi-book"
          active={pathname === "/guide"}
          onNavigate={onNavigate}
        />
      </div>

      <div className="flex flex-col gap-0.5" data-tour="nav-workspaces">
        <span className="nav-section mb-1">Workspaces</span>
        {workspaces.isLoading && <NavSkeleton />}
        {workspaces.data?.length === 0 && (
          <span
            className="px-2 py-1 text-sm"
            style={{ color: "var(--text-muted)" }}
          >
            None yet
          </span>
        )}
        {workspaces.data?.map((ws) => {
          const open = expanded.includes(ws.uuid);
          return (
            <div key={ws.uuid} className="flex flex-col gap-0.5">
              <div className="flex items-center">
                {/* The chevron opens the branch; the name opens the workspace.
                    Seeing what is inside and going inside are two intentions,
                    and one target cannot serve both. */}
                <button
                  type="button"
                  className="nav-chevron"
                  aria-label={`${open ? "Collapse" : "Expand"} ${ws.display_name}`}
                  aria-expanded={open}
                  onClick={() => toggle(ws.uuid)}
                >
                  <i
                    className={
                      open ? "pi pi-chevron-down" : "pi pi-chevron-right"
                    }
                  />
                </button>
                <div className="flex-1 min-w-0">
                  <NavLink
                    to={`/workspaces/${ws.uuid}`}
                    label={ws.display_name}
                    active={pathname === `/workspaces/${ws.uuid}`}
                    onNavigate={onNavigate}
                  />
                </div>
              </div>
              {open && (
                <div className="flex flex-col gap-0.5">
                  <WorkspaceLists
                    workspaceUuid={ws.uuid}
                    activePath={pathname}
                    onNavigate={onNavigate}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {shared.data && shared.data.length > 0 && (
        <div className="flex flex-col gap-0.5">
          <span className="nav-section mb-1">Shared with me</span>
          {shared.data.map((list) => (
            <NavLink
              key={list.uuid}
              to={`/lists/${list.uuid}`}
              label={list.display_name}
              icon="pi pi-table"
              active={pathname === `/lists/${list.uuid}`}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </nav>
  );
}
