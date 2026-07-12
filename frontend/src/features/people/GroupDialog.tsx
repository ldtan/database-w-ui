import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { InputText } from "primereact/inputtext";
import { InputTextarea } from "primereact/inputtextarea";
import { MultiSelect } from "primereact/multiselect";
import { Button } from "primereact/button";

import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { ApiUser, ApiUserGroup } from "../../lib/types";

interface Props {
  visible: boolean;
  onHide: () => void;
  /** null = create a new group. */
  group: ApiUserGroup | null;
  users: ApiUser[];
}

export function GroupDialog({ visible, onHide, group, users }: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const editing = !!group;

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [members, setMembers] = useState<string[]>([]);

  useEffect(() => {
    if (!visible) return;
    setName(group?.name ?? "");
    setDescription(group?.description ?? "");
    setMembers(group?.users.map((u) => u.uuid) ?? []);
  }, [visible, group]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        description: description.trim() || null,
        user_uuids: members,
      };
      return editing
        ? api.patch<ApiUserGroup>(`/auth/groups/${group!.uuid}`, body)
        : api.post<ApiUserGroup>("/auth/groups/", body);
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      toast.success(editing ? "Group saved" : "Group created", saved.name);
      onHide();
    },
    onError: (err) =>
      toast.error(
        editing ? "Could not save group" : "Could not create group",
        err instanceof ApiError ? err.message : undefined,
      ),
  });

  const userOptions = users.map((u) => ({ label: u.username, value: u.uuid }));

  return (
    <Dialog
      header={editing ? `Edit ${group!.name}` : "New group"}
      visible={visible}
      onHide={onHide}
      style={{ width: 460 }}
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <label htmlFor="g-name" className="text-sm font-medium">
            Name
          </label>
          <InputText
            id="g-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="g-desc" className="text-sm font-medium">
            Description{" "}
            <span style={{ color: "var(--text-muted)" }}>(optional)</span>
          </label>
          <InputTextarea
            id="g-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="g-members" className="text-sm font-medium">
            Members
          </label>
          <MultiSelect
            inputId="g-members"
            value={members}
            options={userOptions}
            onChange={(e) => setMembers(e.value)}
            placeholder="Add members…"
            display="chip"
            filter
          />
          <small style={{ color: "var(--text-muted)" }}>
            Members inherit every permission granted to this group.
          </small>
        </div>

        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label={editing ? "Save" : "Create"}
            onClick={() => save.mutate()}
            loading={save.isPending}
            disabled={!name.trim()}
          />
        </div>
      </div>
    </Dialog>
  );
}
