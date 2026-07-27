import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Button } from "primereact/button";

import { api } from "../../lib/api";
import { useCurrentUser } from "../../lib/auth";
import { EmptyState } from "../../components/EmptyState";
import { SkeletonCards } from "../../components/Skeleton";
import { fullName, useMyPerson } from "../../lib/people";
import { useOnboarding } from "../onboarding/OnboardingProvider";
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
      className="surface-card is-interactive no-underline p-4 flex items-center gap-3"
      style={{ color: "var(--text)" }}
    >
      <span className="entity-icon" style={{ width: "2.75rem", height: "2.75rem" }} aria-hidden>
        <i className={icon} style={{ fontSize: "1.15rem" }} />
      </span>
      <span className="flex flex-col">
        <span className="text-2xl font-semibold leading-none">
          {loading ? (
            <span
              className="skeleton inline-block"
              style={{ width: "2ch", height: "1.5rem", verticalAlign: "middle" }}
            />
          ) : (
            value
          )}
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
      className="surface-card is-interactive no-underline p-4 flex items-start gap-3"
      style={{ color: "var(--text)" }}
    >
      <span className="entity-icon" aria-hidden>
        <i className={icon} />
      </span>
      <span className="flex flex-col gap-0.5 min-w-0">
        <span className="font-medium">{title}</span>
        <span className="text-sm" style={{ color: "var(--text-muted)" }}>
          {desc}
        </span>
      </span>
    </Link>
  );
}

/** A compact row linking into a workspace or a shared list. */
function LinkCard({
  to,
  icon,
  title,
  sub,
}: {
  to: string;
  icon: string;
  title: string;
  sub?: string;
}) {
  return (
    <Link
      to={to}
      className="surface-card is-interactive no-underline p-3 flex items-center gap-3"
      style={{ color: "var(--text)" }}
    >
      <span className="entity-icon" aria-hidden>
        <i className={icon} />
      </span>
      <span className="flex flex-col min-w-0">
        <span className="font-medium truncate">{title}</span>
        {sub && (
          <span
            className="text-sm truncate"
            style={{ color: "var(--text-muted)" }}
          >
            {sub}
          </span>
        )}
      </span>
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
    <div className="flex items-center justify-between gap-2">
      <h2 className="text-lg font-semibold">{title}</h2>
      {action}
    </div>
  );
}

export function DashboardPage() {
  const me = useCurrentUser();
  const person = useMyPerson();
  const { startTour } = useOnboarding();
  // The first name when there is a profile, the username when there is not —
  // "Welcome, ana" is a greeting; "Welcome, a.cruz.2" is a login prompt.
  const greeting = person.data?.first_name || me?.username;

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
    <div className="flex flex-col gap-6" style={{ maxWidth: 1100 }}>
      {/* Greeting */}
      <section className="hero flex flex-col gap-4" data-tour="dash-hero">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-2xl font-semibold">
            Welcome{greeting ? `, ${greeting}` : ""} 👋
          </h1>
          <p style={{ color: "var(--text-muted)", maxWidth: "44rem" }}>
            Build flexible databases without writing code — organise everything
            into workspaces, fill lists with records, then filter, share, and
            export.
          </p>
          {person.data && (
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Signed in as {fullName(person.data)}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/workspaces" className="no-underline">
            <Button label="Go to your workspaces" icon="pi pi-th-large" />
          </Link>
          <Link to="/guide" className="no-underline">
            <Button label="Read the guide" icon="pi pi-book" outlined />
          </Link>
          {/* The tour is offered from the help button on every page, but the
              greeting is where somebody who has just arrived is looking. */}
          <Button
            label="Show me around"
            icon="pi pi-compass"
            text
            onClick={() => startTour("basics")}
          />
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
          label={
            sharedTotal === 1 ? "list shared with you" : "lists shared with you"
          }
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
                className="no-underline text-sm font-medium"
                style={{ color: "var(--brand)" }}
              >
                See all →
              </Link>
            ) : undefined
          }
        />
        {workspaces.isLoading ? (
          <SkeletonCards count={3} />
        ) : workspaceItems.length === 0 ? (
          <EmptyState
            icon="pi pi-th-large"
            title="No workspaces yet"
            text="A workspace is a folder that holds your lists — create your first one to get started."
            action={
              <div className="flex flex-wrap gap-2 justify-center">
                <Link to="/workspaces" className="no-underline">
                  <Button label="Create a workspace" icon="pi pi-plus" />
                </Link>
                <Link to="/guide" className="no-underline">
                  <Button label="How this works" icon="pi pi-book" text />
                </Link>
              </div>
            }
          />
        ) : (
          <div className="card-grid">
            {workspaceItems.map((ws) => (
              <LinkCard
                key={ws.uuid}
                to={`/workspaces/${ws.uuid}`}
                icon={ws.is_system ? "pi pi-inbox" : "pi pi-th-large"}
                title={ws.display_name}
                sub={ws.is_system ? "Lists transferred to you" : ws.name}
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
                  className="no-underline text-sm font-medium"
                  style={{ color: "var(--brand)" }}
                >
                  See all →
                </Link>
              ) : undefined
            }
          />
          <div className="card-grid">
            {sharedItems.map((list) => (
              <LinkCard
                key={list.uuid}
                to={`/lists/${list.uuid}`}
                icon="pi pi-table"
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
