import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import type { Column, FilterCondition, View, ViewConfig } from "../../lib/types";
import { FilterConditionsEditor, cleanConditions } from "./FilterConditionsEditor";

interface Props {
  listUuid: string;
  viewUuid: string;
  columns: Column[];
  baseConfig: ViewConfig;
  visible: boolean;
  onHide: () => void;
}

export function FilterDialog({
  listUuid,
  viewUuid,
  columns,
  baseConfig,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const [conjunction, setConjunction] = useState<"and" | "or">("and");
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  useEffect(() => {
    if (!visible) return;
    setConjunction(baseConfig.filters?.conjunction ?? "and");
    setConditions(baseConfig.filters?.conditions ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const apply = useMutation({
    mutationFn: () => {
      const config: ViewConfig = {
        ...baseConfig,
        filters: { conjunction, conditions: cleanConditions(conditions) },
      };
      return api.patch<View>(`/lists/${listUuid}/views/${viewUuid}`, { config });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["views", listUuid] });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      onHide();
    },
  });

  return (
    <Dialog header="Filter" visible={visible} onHide={onHide} style={{ width: 560 }}>
      <div className="flex flex-col gap-3">
        {apply.error instanceof ApiError && (
          <Message severity="error" text={apply.error.message} />
        )}

        <FilterConditionsEditor
          columns={columns}
          conjunction={conjunction}
          onConjunctionChange={setConjunction}
          conditions={conditions}
          onConditionsChange={setConditions}
        />

        <div className="flex justify-end gap-2">
          <Button label="Cancel" text onClick={onHide} />
          <Button
            label="Apply"
            onClick={() => apply.mutate()}
            loading={apply.isPending}
          />
        </div>
      </div>
    </Dialog>
  );
}
