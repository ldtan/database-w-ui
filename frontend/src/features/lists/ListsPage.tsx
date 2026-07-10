import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import type { ListRead } from "../../lib/types";
import { slugify } from "../../lib/slug";

export function ListsPage() {
  const { workspaceUuid = "" } = useParams();
  const queryClient = useQueryClient();

  const lists = useQuery({
    queryKey: ["lists", workspaceUuid],
    queryFn: () =>
      api.get<ListRead[]>(`/lists/?workspace_uuid=${workspaceUuid}`),
  });

  const [displayName, setDisplayName] = useState("");
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<ListRead>("/lists/", {
        name: slugify(name),
        display_name: name,
        workspace_uuid: workspaceUuid,
      }),
    onSuccess: () => {
      setDisplayName("");
      queryClient.invalidateQueries({ queryKey: ["lists", workspaceUuid] });
    },
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    if (displayName.trim()) create.mutate(displayName.trim());
  }

  return (
    <div className="flex flex-col gap-4" style={{ maxWidth: 720 }}>
      <div className="flex items-center gap-2">
        <Link to="/" className="no-underline text-sm text-gray-500">
          ← Workspaces
        </Link>
      </div>
      <h1 className="text-xl font-semibold">Lists</h1>

      <form onSubmit={onCreate} className="flex gap-2">
        <InputText
          placeholder="New list name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="flex-1"
        />
        <Button type="submit" label="Create" icon="pi pi-plus" loading={create.isPending} />
      </form>
      {create.error instanceof ApiError && (
        <Message severity="error" text={create.error.message} />
      )}

      {lists.isLoading && <p>Loading…</p>}
      {lists.data?.length === 0 && <p>No lists in this workspace yet.</p>}

      <div className="flex flex-col gap-2">
        {lists.data?.map((list) => (
          <Link key={list.uuid} to={`/lists/${list.uuid}`} className="no-underline">
            <Card className="cursor-pointer">
              <div className="font-medium">{list.display_name}</div>
              <div className="text-sm text-gray-500">{list.name}</div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
