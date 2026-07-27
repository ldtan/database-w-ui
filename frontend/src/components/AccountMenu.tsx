import { useId, useRef } from "react";
import { Link } from "react-router-dom";
import { Button } from "primereact/button";
import { Menu } from "primereact/menu";

import { useAuth, useCurrentUser } from "../lib/auth";
import { fullName, useMyPerson } from "../lib/people";
import { UserAvatar } from "./UserAvatar";

/**
 * The account control in the header: one avatar, and a menu behind it.
 *
 * It replaces a Profile link and a Log out button that sat in the same row as the
 * navigation — which read them as two more places to go, and put logging out one
 * stray click from opening your profile. Folding them into the avatar also gives the
 * header the thing it never said: which account this is. On a shared machine, or with
 * two of them, that is the first question the header should be able to answer.
 *
 * The identity block at the top is the drawer's, deliberately: the same three lines
 * in the same order, so the two surfaces do not describe the same person differently.
 */
interface Props {
  /**
   * `avatar` — the circle alone, for the header, where the row it sits in is
   * icon buttons and a name would set the header's width from the length of
   * whoever signed in.
   *
   * `row` — the circle with the name beside it, for the foot of the sidebar,
   * which has a column of its own to fill and no other place saying who you are.
   */
  variant?: "avatar" | "row";
}

export function AccountMenu({ variant = "avatar" }: Props) {
  const { logout } = useAuth();
  const me = useCurrentUser();
  const person = useMyPerson();
  const menuRef = useRef<Menu>(null);
  const menuId = useId();

  const name = fullName(person.data);
  // `me` is null until /auth/users/me lands. The button renders anyway — a control
  // that appears a moment after the rest of the header shifts everything beside it.
  const username = me?.username ?? "";

  return (
    <>
      {variant === "row" ? (
        <button
          type="button"
          className="account-row"
          aria-label="Account"
          aria-haspopup
          aria-controls={menuId}
          onClick={(e) => menuRef.current?.toggle(e)}
        >
          <UserAvatar person={person.data} username={username} size="2rem" />
          <span className="flex flex-col min-w-0 text-left">
            <span className="truncate text-sm font-semibold">
              {name || username}
            </span>
            {name && (
              <span
                className="truncate"
                style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}
              >
                {username}
              </span>
            )}
          </span>
          <i
            className="pi pi-angle-up ml-auto"
            style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}
          />
        </button>
      ) : (
        <Button
          text
          rounded
          aria-label="Account"
          aria-haspopup
          aria-controls={menuId}
          style={{ padding: "0.25rem" }}
          onClick={(e) => menuRef.current?.toggle(e)}
        >
          <UserAvatar person={person.data} username={username} size="2.25rem" />
        </Button>
      )}
      <Menu
        popup
        ref={menuRef}
        id={menuId}
        // The theme fixes .p-menu at 12.5rem, which is a width chosen for items the
        // size of "Log out" and cuts an email in half.
        style={{ width: "auto", minWidth: "16rem" }}
        model={[
          {
            // Not a place to go — the answer to "whose account is this?".
            template: (
              <div className="flex items-center gap-3 px-3 py-2">
                <UserAvatar
                  person={person.data}
                  username={username}
                  size="2.25rem"
                />
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
                    {username}
                  </span>
                  <span
                    className="truncate"
                    style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}
                  >
                    {me?.email}
                  </span>
                </span>
              </div>
            ),
          },
          { separator: true },
          {
            label: "Profile",
            icon: "pi pi-user",
            // A real anchor rather than a command, so Profile still opens in a new
            // tab on a middle click — every other way into it in the app is a <Link>.
            //
            // A template replaces the whole item, `.p-menuitem-content` wrapper and
            // all, so that wrapper is restored by hand: without it the row loses the
            // padding and icon spacing every other item has. The colour is set for
            // the same reason — the menu renders in a portal at the end of <body>,
            // where the global `a { color: var(--brand) }` reaches it and Log out is
            // the only one of the pair that still looks like a menu item.
            template: (item, options) => (
              <div className="p-menuitem-content">
                <Link
                  to="/profile"
                  className={options.className}
                  style={{ color: "var(--text)", textDecoration: "none" }}
                  onClick={(e) => menuRef.current?.hide(e)}
                >
                  <span className={options.iconClassName} />
                  <span className={options.labelClassName}>{item.label}</span>
                </Link>
              </div>
            ),
          },
          {
            label: "Log out",
            icon: "pi pi-sign-out",
            command: logout,
          },
        ]}
      />
    </>
  );
}
