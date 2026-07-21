import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "primereact/button";

import { api } from "../../lib/api";
import { useCurrentUser } from "../../lib/auth";
import type { ListRead, Workspace } from "../../lib/types";

/**
 * The home page: a friendly landing that greets you, shows how much you already
 * have, points you back to where you were, and offers the handful of places worth
 * going next — including the Guide for anyone new.
 *
 * Everything here is a shortcut into pages that already exist; the dashboard owns
 * no data of its own. Counts and previews come from the same list endpoints the
 * Workspaces page uses (just a small page of each), so they cost one light request
 * apiece and never block the greeting from rendering.
 */

const PREVIEW = 6;

/** A small clickable metric — a number and what it counts, linking somewhere. */
function StatTile({
  to,
  icon,
  value,
  label,
  loading,
}: {
  to: string;
  icon: string;
  value: number;
  label: string;
  loading: boolean;
}) {
  return (
    <Link
      to={to}
      className="no-underline rounded p-4 flex items-center gap-3"
      style={{
        background: "var(--surface)",
        border: "var(--border-width) solid var(--border)",
      }}
    >
      <span
        className="flex items-center justify-center rounded shrink-0"
        style={{
          width: "2.75rem",
          height: "2.75rem",
          background: "var(--brand-tint)",
          color: "var(--brand)",
        }}
        aria-hidden
      >
        <i className={icon} style={{ fontSize: "1.25rem" }} />
      </span>
      <span className="flex flex-col">
        <span className="text-2xl font-semibold" style={{ color: "var(--text)" }}>
          {loading ? "…" : value}
        </span>
        <span className="text-sm" style={{ color: "var(--text-muted)" }}>
          {label}
        </span>
      </span>
    </Link>
  );
}

/** A "go here next" card: an icon, a title, and a line on what it's for. */
function ActionCard({
  to,
  icon,
  title,
  desc,
}: {
  to: string;
  icon: string;
  title: string;
  desc: string;
}) {
  return (
    <Link
      to={to}
      className="no-underline rounded p-4 flex flex-col gap-1"
      style={{
        background: "var(--surface)",
        border: "var(--border-width) solid var(--border)",
      }}
    >
      <span className="flex items-center gap-2 font-medium" style={{ color: "var(--text)" }}>
        <i className={icon} style={{ color: "var(--brand)" }} />
        {title}
      </span>
      <span className="text-sm" style={{ color: "var(--text-muted)" }}>
        {desc}
      </span>
    </Link>
  );
}

/** A compact row linking into a workspace or a shared list. */
function LinkCard({
  to,
  title,
  sub,
}: {
  to: string;
  title: string;
  sub?: string;
}) {
  return (
    <Link
      to={to}
      className="no-underline rounded px-3 py-2 flex flex-col"
      style={{
        background: "var(--surface)",
        border: "var(--border-width) solid var(--border)",
      }}
    >
      <span className="font-medium truncate" style={{ color: "var(--text)" }}>
        {title}
      </span>
      {sub && (
        <span className="text-sm truncate" style={{ color: "var(--text-muted)" }}>
          {sub}
        </span>
      )}
    </Link>
  );
}

function SectionHeader({
  title,
  action,
}: {
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between">
      <h2 className="text-lg font-medium">{title}</h2>
      {action}
    </div>
  );
}

export function DashboardPage() {
  const me = useCurrentUser();

  // A small page of each: the total (from the count header) drives the tiles, and
  // the first few items give a "jump back in" preview.
  const workspaces = useQuery({
    queryKey: ["workspaces", "overview", PREVIEW],
    queryFn: () => api.getPage<Workspace>(`/workspaces/?skip=0&limit=${PREVIEW}`),
  });
  const shared = useQuery({
    queryKey: ["lists", "shared", "overview", PREVIEW],
    queryFn: () =>
      api.getPage<ListRead>(`/lists/?shared=true&skip=0&limit=${PREVIEW}`),
  });

  const workspaceItems = workspaces.data?.items ?? [];
  const workspaceTotal = workspaces.data?.total ?? 0;
  const sharedItems = shared.data?.items ?? [];
  const sharedTotal = shared.data?.total ?? 0;

  return (
    <div className="flex flex-col gap-6" style={{ maxWidth: 960 }}>
      {/* Greeting */}
      <section
        className="rounded p-5 flex flex-col gap-3"
        style={{
          background: "var(--brand-tint)",
          border: "var(--border-width) solid var(--border)",
        }}
      >
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">
            Welcome{me?.username ? `, ${me.username}` : ""} 👋
          </h1>
          <p style={{ color: "var(--text-muted)" }}>
            Build flexible databases without writing code — organise everything into
            workspaces, fill lists with records, then filter, share, and export.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/workspaces" className="no-underline">
            <Button label="Go to your workspaces" icon="pi pi-th-large" />
          </Link>
          <Link to="/guide" className="no-underline">
            <Button label="Read the guide" icon="pi pi-book" outlined />
          </Link>
        </div>
      </section>

      {/* At-a-glance */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <StatTile
          to="/workspaces"
          icon="pi pi-th-large"
          value={workspaceTotal}
          label={workspaceTotal === 1 ? "workspace" : "workspaces"}
          loading={workspaces.isLoading}
        />
        <StatTile
          to="/workspaces"
          icon="pi pi-share-alt"
          value={sharedTotal}
          label={sharedTotal === 1 ? "list shared with you" : "lists shared with you"}
          loading={shared.isLoading}
        />
      </div>

      {/* Jump back in */}
      <section className="flex flex-col gap-3">
        <SectionHeader
          title="Your workspaces"
          action={
            workspaceTotal > PREVIEW ? (
              <Link
                to="/workspaces"
                className="no-underline text-sm"
                style={{ color: "var(--brand)" }}
              >
                See all →
              </Link>
            ) : undefined
          }
        />
        {workspaces.isLoading ? (
          <p style={{ color: "var(--text-muted)" }}>Loading…</p>
        ) : workspaceItems.length === 0 ? (
          <div
            className="rounded p-4 flex flex-col gap-2"
            style={{
              background: "var(--surface)",
              border: "var(--border-width) solid var(--border)",
            }}
          >
            <p style={{ color: "var(--text-muted)" }}>
              You don’t have any workspaces yet. A workspace is a folder that holds
              your lists — create your first one to get started.
            </p>
            <div className="flex flex-wrap gap-2">
              <Link to="/workspaces" className="no-underline">
                <Button label="Create a workspace" icon="pi pi-plus" />
              </Link>
              <Link to="/guide" className="no-underline">
                <Button label="How this works" icon="pi pi-book" text />
              </Link>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {workspaceItems.map((ws) => (
              <LinkCard
                key={ws.uuid}
                to={`/workspaces/${ws.uuid}`}
                title={ws.display_name}
                sub={ws.name}
              />
            ))}
          </div>
        )}
      </section>

      {/* Shared with me — only when there is something to show */}
      {sharedTotal > 0 && (
        <section className="flex flex-col gap-3">
          <SectionHeader
            title="Shared with you"
            action={
              sharedTotal > PREVIEW ? (
                <Link
                  to="/workspaces"
                  className="no-underline text-sm"
                  style={{ color: "var(--brand)" }}
                >
                  See all →
                </Link>
              ) : undefined
            }
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {sharedItems.map((list) => (
              <LinkCard
                key={list.uuid}
                to={`/lists/${list.uuid}`}
                title={list.display_name}
                sub={
                  list.owner_username
                    ? `owned by ${list.owner_username}`
                    : list.name
                }
              />
            ))}
          </div>
        </section>
      )}

      {/* Where to next */}
      <section className="flex flex-col gap-3">
        <SectionHeader title="Explore" />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <ActionCard
            to="/workspaces"
            icon="pi pi-th-large"
            title="Workspaces & lists"
            desc="Create workspaces, build lists, and add your records."
          />
          <ActionCard
            to="/guide"
            icon="pi pi-book"
            title="Read the guide"
            desc="A plain-language walkthrough, from the basics to sharing and formulas."
          />
          <ActionCard
            to="/people"
            icon="pi pi-users"
            title="People & groups"
            desc="Browse accounts and groups you can share things with."
          />
          <ActionCard
            to="/profile"
            icon="pi pi-user"
            title="Your profile"
            desc="Your account details and personal information."
          />
        </div>
      </section>
    </div>
  );
}
