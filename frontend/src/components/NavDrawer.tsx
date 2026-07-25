import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Sidebar } from "primereact/sidebar";

import { api } from "../lib/api";
import { useAuth, useCurrentUser } from "../lib/auth";
import { fullName, useMyPerson } from "../lib/people";
import type { ListRead, Workspace } from "../lib/types";
import { UserAvatar } from "./UserAvatar";

interface Props {
  visible: boolean;
  onHide: () => void;
}

/** Who you are signed in as, and the way to your profile.
 *
 *  At the top and set apart from the navigation below it: it is not another place
 *  to go, it is the answer to "whose account is this?" — which on a shared phone,
 *  or with two accounts, is the first thing you want the drawer to tell you. */
function ProfileHeader({
  active,
  onNavigate,
}: {
  active: boolean;
  onNavigate: () => void;
}) {
  const me = useCurrentUser();
  const person = useMyPerson();

  if (!me) return null;
  const name = fullName(person.data);

  return (
    <Link
      to="/profile"
      onClick={onNavigate}
      className="no-underline flex items-center gap-3 rounded px-2 py-3"
      style={{ background: active ? "var(--brand-tint)" : "transparent" }}
    >
      <UserAvatar person={person.data} username={me.username} />
      <span className="flex flex-col min-w-0">
        {name && (
          <span
            className="truncate font-semibold"
            style={{ color: "var(--text)" }}
          >
            {name}
          </span>
        )}
        <span
          className="truncate"
          style={{
            color: name ? "var(--text-muted)" : "var(--text)",
            fontWeight: name ? 400 : 600,
          }}
        >
          {me.username}
        </span>
        <span
          className="truncate"
          style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}
        >
          {me.email}
        </span>
      </span>
    </Link>
  );
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
      <span
        className="px-2 py-1 text-sm"
        style={{ color: "var(--text-muted)" }}
      >
        Loading…
      </span>
    );
  }
  if (!lists.data?.length) {
    return (
      <span
        className="px-2 py-1 text-sm"
        style={{ color: "var(--text-muted)" }}
      >
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
    >
      {/* Two bands: your account, then everywhere you can go. The account band holds
          both of the things you do with an account — open your profile, or leave —
          which is the same pairing the header's avatar menu makes, so the two
          surfaces do not teach different habits.

          Only the navigation band scrolls. Keeping the account band above it means a
          long list of workspaces cannot push it off the end of the world, which is
          exactly what a single scrolling column does. */}
      <div className="flex flex-col h-full">
        <ProfileHeader active={pathname === "/profile"} onNavigate={onHide} />
        {/* Its own row under the profile rather than an item in the list below:
            logging out is not a place you go, and it is the one thing here you never
            want to hit by aiming at something else. */}
        {/* The icon and text are children rather than the `icon`/`label` props: those
            render a label with `flex: 1`, which centres the words and leaves Log out
            floating in the middle of the drawer, lined up with nothing above or below
            it. The padding and gap are the nav rows' own, so the icon sits in the
            same column as theirs. */}
        <Button
          text
          className="flex items-center gap-2"
          style={{ justifyContent: "flex-start", padding: "0.5rem" }}
          onClick={() => {
            onHide();
            logout();
          }}
        >
          <i className="pi pi-sign-out" />
          <span>Log out</span>
        </Button>
        <hr
          className="my-2 border-0 border-t"
          style={{ borderColor: "var(--border)" }}
        />

        <nav className="flex flex-col gap-1 flex-1 overflow-y-auto">
          <NavLink
            to="/"
            label="Home"
            icon="pi pi-home"
            active={pathname === "/"}
            onNavigate={onHide}
          />
          <NavLink
            to="/guide"
            label="Guide"
            icon="pi pi-book"
            active={pathname === "/guide"}
            onNavigate={onHide}
          />
          <NavLink
            to="/people"
            label="People"
            icon="pi pi-users"
            active={pathname === "/people"}
            onNavigate={onHide}
          />
          <NavLink
            to="/workspaces"
            label="Workspaces"
            icon="pi pi-th-large"
            active={pathname === "/workspaces"}
            onNavigate={onHide}
          />

          {workspaces.isLoading && (
            <span
              className="px-2 text-sm"
              style={{ color: "var(--text-muted)" }}
            >
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
        </nav>
      </div>
    </Sidebar>
  );
}
