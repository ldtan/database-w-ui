import { Link, useLocation } from "react-router-dom";
import { Button } from "primereact/button";
import { Sidebar } from "primereact/sidebar";

import { useAuth, useCurrentUser } from "../lib/auth";
import { fullName, useMyPerson } from "../lib/people";
import { NavTree } from "./NavTree";
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

/**
 * The navigation, for a window too narrow to give it a permanent rail.
 *
 * It carries the very same `NavTree` the desktop sidebar does — workspaces,
 * their lists, and the lists shared with you — and adds the one band the rail
 * puts in its footer instead: your account, and the way out.
 *
 * Nothing is fetched until it is first opened, and a workspace's lists not until
 * that workspace is expanded.
 */
export function NavDrawer({ visible, onHide }: Props) {
  const { logout } = useAuth();
  const { pathname } = useLocation();

  return (
    <Sidebar
      visible={visible}
      onHide={onHide}
      position="left"
      style={{ width: "min(20rem, 85vw)" }}
    >
      {/* Two bands: your account, then everywhere you can go. The account band
          holds both of the things you do with an account — open your profile, or
          leave — which is the same pairing the header's avatar menu makes, so the
          two surfaces do not teach different habits.

          Only the navigation band scrolls. Keeping the account band above it
          means a long list of workspaces cannot push it off the end of the
          world, which is exactly what a single scrolling column does. */}
      <div className="flex flex-col h-full">
        <ProfileHeader active={pathname === "/profile"} onNavigate={onHide} />
        {/* Its own row under the profile rather than an item in the list below:
            logging out is not a place you go, and it is the one thing here you
            never want to hit by aiming at something else. */}
        {/* The icon and text are children rather than the `icon`/`label` props:
            those render a label with `flex: 1`, which centres the words and
            leaves Log out floating in the middle of the drawer, lined up with
            nothing above or below it. The padding and gap are the nav rows' own,
            so the icon sits in the same column as theirs. */}
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

        <div className="flex-1 overflow-y-auto">
          <NavTree enabled={visible} onNavigate={onHide} />
        </div>
      </div>
    </Sidebar>
  );
}
