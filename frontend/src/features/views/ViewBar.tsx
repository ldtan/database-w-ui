import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dropdown } from "primereact/dropdown";
import { Button } from "primereact/button";
import { AppDialog } from "../../components/AppDialog";
import { InputText } from "primereact/inputtext";
import { Message } from "primereact/message";
import { confirmDialog } from "primereact/confirmdialog";

import { api, ApiError } from "../../lib/api";
import { Field } from "../../components/Field";
import { slugify } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { View, ViewConfig } from "../../lib/types";
import { ShareDialog } from "../permissions/ShareDialog";
import {
  useEffectiveActions,
  useEffectiveActionsMap,
} from "../permissions/useEffectiveActions";

interface Props {
  listUuid: string;
  views: View[];
  activeUuid: string | null;
  onSelect: (uuid: string) => void;
  currentConfig: ViewConfig;
  // Sharing a view is configuring it, not using it, so the toolbar leaves it out
  // and List settings → Views offers it. Same bar, one button apart.
  showShare?: boolean;
  /**
   * Whether the page's live state has drifted from what this view has stored.
   *
   * It decides whether "Save view" is *enabled*, not whether it is there. Both
   * have been tried: the button first sat on the toolbar permanently, reading as
   * one of the things you do to a list rather than as an offer to keep the
   * arrangement you just made; hiding it until there was something to save fixed
   * that and introduced two worse problems. You could only discover the button by
   * changing something first, which is the wrong order to learn a control in, and
   * it appeared mid-toolbar and pushed everything right of it sideways, under a
   * cursor already on its way to another button.
   *
   * Disabled keeps the signal — nothing to save, and you can see that at a glance
   * — without either. Undefined means the caller does not track drift, and the
   * button stays enabled.
   */
  dirty?: boolean;
}

/**
 * Choose a view, save the current one, share it, add another.
 *
 * Every one of those degrades on its own, so this renders on a list with no views
 * at all — where it reduces to a single "New view" button. That is the case it
 * matters most in: the way you make a list's first view is to arrange the records
 * the way you want and keep the arrangement, and the button that keeps it lives
 * here. The bar used to be hidden in exactly that situation, which took the
 * filter, grouping and column controls with it.
 *
 * "Save view" writes the *page's* live state (`currentConfig` — sort, filter,
 * grouping, hidden columns, page size) into the view. Nothing the toolbar does
 * persists until that button is pressed: a view is a saved arrangement, and changing
 * what you are looking at is not the same as changing what everyone else will look
 * at. The filter joined that list when filtering stopped writing straight to the
 * view; it was the one control that changed a shared object the moment you used it.
 *
 * A view can carry its own grants, so who may save or share *this* view is asked
 * per view, while adding one is a permission on the list.
 */
export function ViewBar({
  listUuid,
  views,
  activeUuid,
  onSelect,
  currentConfig,
  showShare = false,
  dirty,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["views", listUuid] });
    // Sort/filter are applied server-side by view, so records must refetch too.
    queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    queryClient.invalidateQueries({ queryKey: ["effective"] });
  };

  const activeView = views.find((v) => v.uuid === activeUuid) ?? null;
  // Adding a view is a list-level change; saving or sharing one is scoped to
  // that view, which may carry its own grants.
  const listActions = useEffectiveActions("list", listUuid);
  const canView = useEffectiveActionsMap(
    "view",
    views.map((view) => view.uuid),
  );
  const canSave = !!activeView && canView(activeView.uuid, "configure");
  const canShare = !!activeView && canView(activeView.uuid, "share");
  // Spelled out where it stands alone: the settings tab, and a list with no views.
  const spellOutNew = showShare || views.length === 0;

  const save = useMutation({
    mutationFn: () =>
      api.patch<View>(`/lists/${listUuid}/views/${activeUuid}`, {
        config: currentConfig,
      }),
    onSuccess: () => {
      invalidate();
      toast.success("View saved");
    },
    onError: (err) =>
      toast.error(
        "Could not save view",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  // Saving overwrites the view's stored sort/filter/columns, so confirm first.
  function confirmSave() {
    confirmDialog({
      message: `Overwrite "${
        activeView?.display_name ?? "this view"
      }" with the current sort, filter, and columns?`,
      header: "Save view",
      icon: "pi pi-save",
      acceptLabel: "Save",
      accept: () => save.mutate(),
    });
  }

  const [sharing, setSharing] = useState(false);
  const setInherit = useMutation({
    mutationFn: (value: boolean) =>
      api.patch(`/lists/${listUuid}/views/${activeUuid}`, {
        permissions_inherit: value,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["views", listUuid] }),
    onError: (err) =>
      toast.error(
        "Could not update inheritance",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api.post<View>(`/lists/${listUuid}/views/`, {
        name: slugify(newName),
        display_name: newName,
        config: currentConfig,
      }),
    onSuccess: (view) => {
      setCreating(false);
      setNewName("");
      invalidate();
      onSelect(view.uuid);
      toast.success("View created", view.display_name);
    },
  });

  return (
    <div className="flex flex-wrap items-center gap-1">
      {/* A picker with nothing to pick is worse than no picker: it takes the width
          of the widest control on the bar to say "View", and implies there are
          views to choose between. What a list with no views needs is the button
          below, not this. */}
      {views.length > 0 && (
        <Dropdown
          value={activeUuid}
          options={views.map((v) => ({ label: v.display_name, value: v.uuid }))}
          onChange={(e) => onSelect(e.value as string)}
          placeholder="View"
          aria-label="View"
          className="w-36 sm:w-48"
        />
      )}
      {/* Outlined once there is something to save, and a plain text button while
          there is not: it is the odd control on the bar that changes what *other
          people* will see, so it should only draw the eye when pressing it would
          actually do that.

          **Inert rather than `disabled`, and that distinction is load-bearing.**
          The greyed-out state has to be able to explain itself, and a `disabled`
          button takes no pointer events, so its own tooltip never opens. The
          PrimeReact answer to that is `tooltipOptions.showOnDisabled`, and the way
          it is implemented is that `Tooltip`'s `getTarget` creates a `<div>` and
          moves the button inside it — DOM React did not build and does not know
          about. React goes on believing this button is a child of the toolbar, so
          the next time it unmounts one it removes it from the wrong parent, throws
          `NotFoundError` inside the commit phase, and takes the entire page down
          with it. That is not hypothetical here: `canSave` goes false for about a
          second every time a view is created or deleted, because the batch
          permission query below re-keys on the new set of view uuids and has no
          answer for them while it is in flight.

          `.is-inert` (see themes.css) is the same look and the same refusal
          without the DOM surgery, and it leaves the button focusable — which is
          where "why is this off?" was hardest to answer before, since a `disabled`
          button is out of the tab order and its tooltip was mouse-only anyway. */}
      {canSave && (
        <Button
          label="Save view"
          icon="pi pi-save"
          outlined={dirty === true}
          text={dirty !== true}
          size="small"
          className={dirty === false ? "is-inert" : undefined}
          aria-disabled={dirty === false}
          onClick={() => {
            if (dirty === false) return; // inert, not disabled — see above
            confirmSave();
          }}
          loading={save.isPending}
          tooltip={
            dirty === false
              ? "Nothing to save — this view already looks like this"
              : "Save the current sort, filter, grouping and columns to this view"
          }
          tooltipOptions={{ position: "bottom" }}
        />
      )}
      {showShare && canShare && (
        <Button
          label="Share"
          icon="pi pi-share-alt"
          text
          onClick={() => setSharing(true)}
        />
      )}
      {listActions.has("configure") && (
        // Icon-only on the toolbar, where it is a rarity beside controls used
        // constantly; the settings tab has the room to spell it out. So does a
        // list with no views, where there is no dropdown beside it to lend it
        // context and a lone "+" among the record controls says nothing about
        // what it adds — which is the one moment this button matters most.
        <Button
          icon="pi pi-plus"
          text
          label={spellOutNew ? "New view" : undefined}
          aria-label="New view"
          tooltip={spellOutNew ? undefined : "New view"}
          tooltipOptions={{ position: "bottom" }}
          onClick={() => setCreating(true)}
        />
      )}

      {sharing && activeView && (
        <ShareDialog
          visible={sharing}
          onHide={() => setSharing(false)}
          resourceType="view"
          resourceUuid={activeView.uuid}
          resourceLabel={activeView.display_name}
          inherit={{
            value: activeView.permissions_inherit,
            onChange: (value) => setInherit.mutate(value),
            busy: setInherit.isPending,
          }}
        />
      )}

      <AppDialog
        header="New view"
        visible={creating}
        onHide={() => setCreating(false)}
        style={{ width: 420 }}
        footer={
          <>
            <Button label="Cancel" text onClick={() => setCreating(false)} />
            <Button
              label="Create view"
              icon="pi pi-plus"
              onClick={() => create.mutate()}
              loading={create.isPending}
              disabled={!newName.trim()}
            />
          </>
        }
      >
        <div className="flex flex-col gap-4">
          {create.error instanceof ApiError && (
            <Message severity="error" text={create.error.message} />
          )}
          <Field
            label="View name"
            htmlFor="new-view-name"
            hint="It starts from the sort, filter, grouping and columns showing right now."
          >
            <InputText
              id="new-view-name"
              placeholder="Active only"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              autoFocus
            />
          </Field>
        </div>
      </AppDialog>
    </div>
  );
}
