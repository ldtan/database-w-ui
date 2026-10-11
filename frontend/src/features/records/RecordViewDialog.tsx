import { AppDialog } from "../../components/AppDialog";
import { Button } from "primereact/button";

import type { Column, Form, TableRecord } from "../../lib/types";
import type { PrincipalType } from "../principals/PrincipalControls";
import { resolveFields } from "./recordValues";
import { renderCell } from "./renderCell";

interface Props {
  tableUuid: string;
  columns: Column[];
  /** The table's `view` form, whose config decides which fields show and in what
   *  order. Null renders every column — the dialog stays usable on a table whose
   *  form is missing. */
  form: Form | null;
  item: TableRecord | null;
  /** The reference uuids the *page* holds, passed straight through to renderCell.
   *  Sharing the page's arrays is what makes the labels come from cache instead of
   *  a second request — see the note on the collectors in TableDetailPage. */
  principalUuids: Record<PrincipalType, string[]>;
  lookupUuids: Map<string, string[]>;
  canEdit: boolean;
  onEdit: () => void;
  visible: boolean;
  onHide: () => void;
}

/**
 * One record, read-only, laid out by the table's view form.
 *
 * This exists so a *view* does not have to carry every column to stay useful. A
 * grid is for scanning many records and goes unreadable past a dozen columns; the
 * fields you only ever read live here instead, one per row, for the one record you
 * opened.
 *
 * Two things follow from it being a display surface rather than an input one:
 *
 * - **Calculated columns are included.** They are computed on read and rejected on
 *   write, so `resolveFields` drops them from create/update — but the value is
 *   already in `item.data` under the column's uuid, and showing it is most of the
 *   point of this dialog.
 * - **`enabled` is ignored.** Nothing here is editable, so a form field's
 *   editable-flag has nothing to say; `included`, `order` and `label_override` are
 *   the parts of the config that mean anything.
 *
 * Values go through the shared `renderCell` — the same function the grid, the cards
 * and the bin use — so a link is an anchor, a temporal is formatted and an empty
 * cell is an em dash, here as everywhere else. A fourth way to draw a cell is a
 * fourth way to disagree about one.
 */
export function RecordViewDialog({
  tableUuid,
  columns,
  form,
  item,
  principalUuids,
  lookupUuids,
  canEdit,
  onEdit,
  visible,
  onHide,
}: Props) {
  const fields = resolveFields(columns, form, { includeCalculated: true });

  return (
    <AppDialog
      header="View record"
      visible={visible && !!item}
      onHide={onHide}
      style={{ width: 480 }}
      footer={
        <>
          <Button label="Close" text onClick={onHide} />
          {canEdit && (
            <Button label="Edit" icon="pi pi-pencil" onClick={onEdit} />
          )}
        </>
      }
    >
      {fields.length === 0 && (
        <p className="field-hint">This form has no fields to show.</p>
      )}
      {/* A definition table rather than a stack of rows: hairlines between the
          fields are what let the eye find a label without reading every one. */}
      <dl className="detail-table">
        {item &&
          fields.map(({ column, label }) => (
            <div key={column.uuid} className="detail-row">
              <dt className="detail-label">{label}</dt>
              <dd className="detail-value">
                {/* "full": this is the one surface with room to show a
                    multiline value as more than one line. */}
                {renderCell(
                  column,
                  item,
                  tableUuid,
                  principalUuids,
                  lookupUuids,
                  "full",
                )}
              </dd>
            </div>
          ))}
      </dl>
    </AppDialog>
  );
}
