import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import type { Workspace } from "../../lib/types";
import { slugify } from "../../lib/slug";

export function WorkspacesPage() {
  const queryClient = useQueryClient();
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Workspace[]>("/workspaces/"),
  });

  const [displayName, setDisplayName] = useState("");
  const create = useMutation({
    mutationFn: (name: string) =>
      api.post<Workspace>("/workspaces/", {
        name: slugify(name),
        display_name: name,
      }),
    onSuccess: () => {
      setDisplayName("");
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });

  function onCreate(event: FormEvent) {
    event.preventDefault();
    if (displayName.trim()) create.mutate(displayName.trim());
  }

  return (
    <div className="flex flex-col gap-4" style={{ maxWidth: 720 }}>
      <h1 className="text-xl font-semibold">Workspaces</h1>

      <form onSubmit={onCreate} className="flex gap-2">
        <InputText
          placeholder="New workspace name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="flex-1"
        />
        <Button type="submit" label="Create" icon="pi pi-plus" loading={create.isPending} />
      </form>
      {create.error instanceof ApiError && (
        <Message severity="error" text={create.error.message} />
      )}

      {workspaces.isLoading && <p>Loading…</p>}
      {workspaces.data?.length === 0 && <p>No workspaces yet.</p>}

      <div className="flex flex-col gap-2">
        {workspaces.data?.map((ws) => (
          <Link key={ws.uuid} to={`/workspaces/${ws.uuid}`} className="no-underline">
            <Card className="cursor-pointer">
              <div className="font-medium">{ws.display_name}</div>
              <div className="text-sm text-gray-500">{ws.name}</div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
