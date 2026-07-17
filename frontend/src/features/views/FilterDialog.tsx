import { useEffect, useState } from "react";
import { AppDialog } from "../../components/AppDialog";
import { Button } from "primereact/button";

import type { Column, FilterCondition, ViewConfig } from "../../lib/types";
import { FilterConditionsEditor, cleanConditions } from "./FilterConditionsEditor";

interface Props {
  listUuid: string;
  viewUuid: string;
  columns: Column[];
  baseConfig: ViewConfig;
  onApply?: (config: ViewConfig) => void;
  visible: boolean;
  onHide: () => void;
}

/**
 * Edit the active view's filter.
 *
 * Applying a filter updates the page's live view state for the current session.
 * It is persisted only when the user explicitly saves the view later.
 *
 * The conditions themselves are edited by `FilterConditionsEditor`, which the
 * import dialog also uses to pick which records to export.
 */
export function FilterDialog({
  listUuid,
  columns,
  baseConfig,
  onApply,
  visible,
  onHide,
}: Props) {
  const [conjunction, setConjunction] = useState<"and" | "or">("and");
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  useEffect(() => {
    if (!visible) return;
    setConjunction(baseConfig.filters?.conjunction ?? "and");
    setConditions(baseConfig.filters?.conditions ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  function handleApply() {
    const config: ViewConfig = {
      ...baseConfig,
      filters: { conjunction, conditions: cleanConditions(conditions) },
    };
    onApply?.(config);
    onHide();
  }

  return (
    <AppDialog header="Filter" visible={visible} onHide={onHide} style={{ width: 560 }}>
      <div className="flex flex-col gap-3">

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
          <Button label="Apply" onClick={handleApply} />
        </div>
      </div>
    </AppDialog>
  );
}
