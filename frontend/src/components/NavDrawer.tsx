import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Sidebar } from "primereact/sidebar";

import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { ListRead, Workspace } from "../lib/types";

interface Props {
  visible: boolean;
  onHide: () => void;
}

/** A row in the drawer: a link that closes the drawer behind it, and shows where
 *  you already are rather than offering to take you there again. */
function NavLink({
  to,
  label,
  sub,
  icon,
  active,
  indent,
  onNavigate,
}: {
  to: string;
  label: string;
  sub?: string;
  icon?: string;
  active: boolean;
  indent?: boolean;
  onNavigate: () => void;
}) {
  return (
    <Link
      to={to}
      onClick={onNavigate}
      className="no-underline rounded px-2 py-2 flex items-center gap-2"
      style={{
        background: active ? "var(--brand-tint)" : "transparent",
        color: active ? "var(--brand)" : "var(--text)",
        fontWeight: active ? 600 : 400,
        marginLeft: indent ? "1.25rem" : 0,
      }}
    >
      {icon && <i className={icon} />}
      <span className="flex flex-col min-w-0">
        <span className="truncate">{label}</span>
        {sub && (
          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
            {sub}
          </span>
        )}
      </span>
    </Link>
  );
}

/** The lists of one workspace, fetched only once it is expanded — a drawer that
 *  loaded every list of every workspace on open would spend a phone's network on
 *  branches nobody looked at. The query key is the one ListsPage uses, so opening
 *  the drawer on a workspace you have already visited costs nothing. */
function WorkspaceLists({
  workspaceUuid,
  activePath,
  onNavigate,
}: {
  workspaceUuid: string;
  activePath: string;
  onNavigate: () => void;
}) {
  const lists = useQuery({
    queryKey: ["lists", workspaceUuid],
    queryFn: () =>
      api.get<ListRead[]>(`/lists/?workspace_uuid=${workspaceUuid}`),
  });

  if (lists.isLoading) {
    return (
      <span className="px-2 py-1 text-sm" style={{ color: "var(--text-muted)" }}>
        Loading…
      </span>
    );
  }
  if (!lists.data?.length) {
    return (
      <span className="px-2 py-1 text-sm" style={{ color: "var(--text-muted)" }}>
        No lists yet.
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

/**
 * The phone's navigation: workspaces, their lists, and the account items.
 *
 * It exists because reaching a list on a phone otherwise meant going home,
 * choosing a workspace, then choosing the list — three taps and two page loads,
 * every time. A popup menu of three account links would not have fixed that, which
 * is the whole reason this is a drawer and not a menu.
 *
 * Nothing is fetched until it is first opened, and a workspace's lists not until
 * that workspace is expanded.
 */
export function NavDrawer({ visible, onHide }: Props) {
  const { logout } = useAuth();
  const { pathname } = useLocation();
  const [expanded, setExpanded] = useState<string[]>([]);

  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Workspace[]>("/workspaces/"),
    enabled: visible,
  });
  // Lists shared with you directly: their workspace is not yours to see, so they
  // hang off no branch of the tree above and would be unreachable from here.
  const shared = useQuery({
    queryKey: ["lists", "shared"],
    queryFn: () => api.get<ListRead[]>("/lists/?shared=true"),
    enabled: visible,
  });

  function toggle(uuid: string) {
    setExpanded((prev) =>
      prev.includes(uuid) ? prev.filter((u) => u !== uuid) : [...prev, uuid],
    );
  }

  return (
    <Sidebar
      visible={visible}
      onHide={onHide}
      position="left"
      style={{ width: "min(20rem, 85vw)" }}
      header={<span className="font-semibold">Database UI</span>}
    >
      <nav className="flex flex-col gap-1">
        <NavLink
          to="/"
          label="Workspaces"
          icon="pi pi-home"
          active={pathname === "/"}
          onNavigate={onHide}
        />

        {workspaces.isLoading && (
          <span className="px-2 text-sm" style={{ color: "var(--text-muted)" }}>
            Loading…
          </span>
        )}
        {workspaces.data?.map((ws) => (
          <div key={ws.uuid} className="flex flex-col gap-1">
            <div className="flex items-center">
              {/* The chevron opens the branch; the name goes to the workspace.
                  Tapping a workspace to see its lists and tapping it to open it are
                  different intentions, and one target cannot serve both. */}
              <Button
                icon={
                  expanded.includes(ws.uuid)
                    ? "pi pi-chevron-down"
                    : "pi pi-chevron-right"
                }
                text
                size="small"
                aria-label={
                  expanded.includes(ws.uuid)
                    ? `Collapse ${ws.display_name}`
                    : `Expand ${ws.display_name}`
                }
                aria-expanded={expanded.includes(ws.uuid)}
                onClick={() => toggle(ws.uuid)}
              />
              <div className="flex-1 min-w-0">
                <NavLink
                  to={`/workspaces/${ws.uuid}`}
                  label={ws.display_name}
                  active={pathname === `/workspaces/${ws.uuid}`}
                  onNavigate={onHide}
                />
              </div>
            </div>
            {expanded.includes(ws.uuid) && (
              <div className="flex flex-col gap-1">
                <WorkspaceLists
                  workspaceUuid={ws.uuid}
                  activePath={pathname}
                  onNavigate={onHide}
                />
              </div>
            )}
          </div>
        ))}

        {shared.data && shared.data.length > 0 && (
          <>
            <span
              className="px-2 pt-3 text-sm font-medium"
              style={{ color: "var(--text-muted)" }}
            >
              Shared with me
            </span>
            {shared.data.map((list) => (
              <NavLink
                key={list.uuid}
                to={`/lists/${list.uuid}`}
                label={list.display_name}
                icon="pi pi-table"
                active={pathname === `/lists/${list.uuid}`}
                onNavigate={onHide}
              />
            ))}
          </>
        )}

        <hr
          className="my-3 border-0 border-t"
          style={{ borderColor: "var(--border)" }}
        />

        <NavLink
          to="/people"
          label="People"
          icon="pi pi-users"
          active={pathname === "/people"}
          onNavigate={onHide}
        />
        <NavLink
          to="/profile"
          label="Profile"
          icon="pi pi-user"
          active={pathname === "/profile"}
          onNavigate={onHide}
        />
        <Button
          label="Log out"
          icon="pi pi-sign-out"
          text
          className="justify-start"
          onClick={() => {
            onHide();
            logout();
          }}
        />
      </nav>
    </Sidebar>
  );
}
