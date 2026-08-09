import { useEffect, useState } from "react";
import { AppDialog } from "../../components/AppDialog";
import { Button } from "primereact/button";

import type { Column, FilterCondition, FilterGroup } from "../../lib/types";
import { FilterConditionsEditor } from "./FilterConditionsEditor";
import { cleanConditions } from "./operators";

interface Props {
  listUuid: string;
  columns: Column[];
  /** The filter currently in effect, which this opens on. */
  value: FilterGroup;
  onApply: (next: FilterGroup) => void;
  visible: boolean;
  onHide: () => void;
}

/**
 * Edit the filter the grid is reading through.
 *
 * This used to PATCH the view the moment you pressed Apply, which made filtering
 * three things it should never have been: permanent, shared with everyone who can
 * see that view, and gated on `configure`. So the most ordinary question you can
 * ask a list — "just the overdue ones" — was a change to the list, and someone
 * with read access could not ask it at all.
 *
 * Now it hands the conditions back to the page, which sends them with the next
 * read (`?filters=`). They are page state exactly like the sort, the grouping and
 * the column order, and they travel into the view by the one route all of those
 * share: the "Save view" button, when you decide the question was worth keeping.
 */
export function FilterDialog({
  listUuid,
  columns,
  value,
  onApply,
  visible,
  onHide,
}: Props) {
  const [conjunction, setConjunction] = useState<"and" | "or">("and");
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  useEffect(() => {
    if (!visible) return;
    setConjunction(value.conjunction ?? "and");
    setConditions(value.conditions ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  function apply() {
    onApply({ conjunction, conditions: cleanConditions(conditions) });
    onHide();
  }

  return (
    <AppDialog
      header="Filter records"
      visible={visible}
      onHide={onHide}
      style={{ width: 620 }}
      footer={
        <>
          {(value.conditions?.length ?? 0) > 0 && (
            <Button
              label="Clear"
              text
              onClick={() => {
                onApply({ conjunction: "and", conditions: [] });
                onHide();
              }}
            />
          )}
          <Button label="Cancel" text onClick={onHide} />
          <Button label="Apply" icon="pi pi-check" onClick={apply} />
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <FilterConditionsEditor
          listUuid={listUuid}
          columns={columns}
          conjunction={conjunction}
          onConjunctionChange={setConjunction}
          conditions={conditions}
          onConditionsChange={setConditions}
        />
      </div>
    </AppDialog>
  );
}
