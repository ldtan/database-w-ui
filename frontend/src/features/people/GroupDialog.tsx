import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
import { InputTextarea } from "primereact/inputtextarea";
import { MultiSelect } from "primereact/multiselect";
import { Button } from "primereact/button";

import { api, ApiError } from "../../lib/api";
import { Field } from "../../components/Field";
import { NameFields, type NamePair } from "../../components/NameFields";
import { isValidName } from "../../lib/slug";
import { useToast } from "../../lib/toast";
import type { ApiUser, ApiUserGroup } from "../../lib/types";

interface Props {
  visible: boolean;
  onHide: () => void;
  /** null = create a new group. */
  group: ApiUserGroup | null;
}

/**
 * Create or edit a group and its membership.
 *
 * A group is a *principal*: it can hold grants, and everyone in it inherits them.
 * So adding someone to a group here hands them, silently, every permission that
 * group has anywhere in the app — which is why this dialog is superuser-only, and
 * why membership is worth more care than the plain table of checkboxes suggests.
 *
 * The `name`/`display_name` pair carries more weight here than it does for a table.
 * A group's slug is what a batch-user CSV's `groups` column and an exported `group`
 * cell name it by, so changing it after the fact invalidates files people are
 * holding — hence `linked={false}` when editing, the same as everywhere else.
 */
export function GroupDialog({ visible, onHide, group }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const editing = !!group;

  // Its own fetch, not the page's: that table is filtered by the page's search box,
  // and the member picker must offer every user regardless of what is typed there.
  const users = useQuery({
    queryKey: ["users", "all"],
    queryFn: () => api.get<ApiUser[]>("/auth/users/?limit=1000"),
    enabled: visible,
  });

  const [names, setNames] = useState<NamePair>({ displayName: "", name: "" });
  const [description, setDescription] = useState("");
  const [members, setMembers] = useState<string[]>([]);

  useEffect(() => {
    if (!visible) return;
    setNames({
      displayName: group?.display_name ?? "",
      name: group?.name ?? "",
    });
    setDescription(group?.description ?? "");
    setMembers(group?.users.map((u) => u.uuid) ?? []);
  }, [visible, group]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: names.name.trim(),
        display_name: names.displayName.trim(),
        description: description.trim() || null,
        user_uuids: members,
      };
      return editing
        ? api.patch<ApiUserGroup>(`/auth/groups/${group!.uuid}`, body)
        : api.post<ApiUserGroup>("/auth/groups/", body);
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      toast.success(
        editing ? "Group saved" : "Group created",
        saved.display_name,
      );
      onHide();
    },
    onError: (err) =>
      toast.error(
        editing ? "Could not save group" : "Could not create group",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const userOptions = (users.data ?? []).map((u) => ({
    label: u.username,
    value: u.uuid,
  }));

  return (
    <AppDialog
      header={editing ? `Edit ${group!.display_name}` : "New group"}
      visible={visible}
      onHide={onHide}
      style={{ width: 460 }}
      footer={
        <>
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={editing ? "Save" : "Create group"}
            icon={editing ? "pi pi-check" : "pi pi-plus"}
            onClick={() => save.mutate()}
            loading={save.isPending}
            disabled={!names.displayName.trim() || !isValidName(names.name)}
          />
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <NameFields
          key={group?.uuid ?? "new"}
          idPrefix="group"
          value={names}
          onChange={setNames}
          linked={!editing}
          autoFocus
        />
        <Field label="Description" htmlFor="g-desc" optional>
          <InputTextarea
            id="g-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
          />
        </Field>
        <Field
          label="Members"
          htmlFor="g-members"
          hint="Members inherit every permission granted to this group."
        >
          <MultiSelect
            inputId="g-members"
            value={members}
            options={userOptions}
            onChange={(e) => setMembers(e.value)}
            placeholder="Add members…"
            display="chip"
            filter
            className="w-full"
          />
        </Field>
      </div>
    </AppDialog>
  );
}
