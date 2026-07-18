import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
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

/**
 * Edit the active view's filter.
 *
 * Unlike the rest of the toolbar, this saves immediately: a filter lives on the
 * view, so changing it *is* a change to the view (hence the `configure` permission
 * the page checks before offering the button). It PATCHes the whole config —
 * `baseConfig` is the page's current state, so applying a filter does not silently
 * discard the sort or column order you had set but not saved.
 *
 * The conditions themselves are edited by `FilterConditionsEditor`, which the
 * import dialog also uses to pick which records to export.
 */
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
    <AppDialog header="Filter" visible={visible} onHide={onHide} style={{ width: 560 }}>
      <div className="flex flex-col gap-3">
        {apply.error instanceof ApiError && (
          <Message severity="error" text={apply.error.message} />
        )}

        <FilterConditionsEditor
          listUuid={listUuid}
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
    </AppDialog>
  );
}
