import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AppDialog } from "../../components/AppDialog";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { SelectButton } from "primereact/selectbutton";
import { Dropdown } from "primereact/dropdown";
import { InputNumber } from "primereact/inputnumber";
import { TabPanel, TabView } from "primereact/tabview";
import {
  FileUpload,
  type FileUploadHandlerEvent,
} from "primereact/fileupload";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { Field, FieldRow, FormSection } from "../../components/Field";
import { useToast } from "../../lib/toast";
import { toCsv } from "../../lib/csv";
import { downloadBlob, downloadFile } from "../../lib/download";
import type { Column, FilterCondition } from "../../lib/types";
import {
  FilterConditionsEditor,
  cleanConditions,
} from "../views/FilterConditionsEditor";

interface Props {
  listUuid: string;
  listName: string;
  columns: Column[];
  // Import writes records; export only reads them.
  canImport: boolean;
  visible: boolean;
  onHide: () => void;
}

type Format = "csv" | "json";

interface ImportResult {
  created: number;
  errors: { row: number; message: string }[];
}

/**
 * Get records out of a list, and back into one.
 *
 * Both halves are keyed by column **name**, not uuid: a file whose headers read
 * `first_name` is one a person can write and a spreadsheet can produce, and it
 * survives a column being renamed in the UI. Reference columns (lookup, user,
 * group) export the *label* of what they point at, not the uuid, for the same
 * reason, and import resolves those labels back, so a file round-trips.
 *
 * Export can be narrowed by a filter (the same `FilterConditionsEditor` the view's
 * Filter dialog uses). Import is offered only with `create` **and** `edit` on the
 * list, since a file can do both; it reports per-row errors rather than failing the
 * whole file.
 *
 * Calculated columns are absent from both halves — see `transferable` below.
 */
export function ImportExportDialog({
  listUuid,
  listName,
  columns,
  canImport,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  // 0 = Export, 1 = Import. Only ever 0 when the caller may not import.
  const [tab, setTab] = useState(0);
  const [format, setFormat] = useState<Format>("csv");
  // The template is an import aid, so it has its own format rather than
  // following the export half's: which file you want to *fill in* has nothing
  // to do with which one you last took data out in.
  const [templateFormat, setTemplateFormat] = useState<Format>("csv");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  // Export query options.
  const [limit, setLimit] = useState<number | null>(null); // null = all rows
  const [sortColumn, setSortColumn] = useState<string | null>(null); // null = id
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [conjunction, setConjunction] = useState<"and" | "or">("and");
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  // The columns a file can carry. A calculated column is derived from the others
  // when a record is read and stores nothing of its own, so it is read-only on the
  // way in and `export_rows` already drops it on the way out. Offering one here is
  // a lie either way round: a template heading you are invited to fill in whose
  // values the import then discards, or a ticked export column that never reaches
  // the file.
  const transferable = useMemo(
    () => columns.filter((c) => c.type !== "calculated"),
    [columns],
  );

  // Default to all columns selected whenever the dialog opens.
  useEffect(() => {
    if (visible) {
      setSelected(new Set(transferable.map((c) => c.uuid)));
      setResult(null);
      setExportError(null);
    }
  }, [visible, transferable]);

  const chosen = transferable.filter((c) => selected.has(c.uuid));

  function toggle(uuid: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(uuid);
      else next.delete(uuid);
      return next;
    });
  }

  async function handleExport() {
    setBusy(true);
    setExportError(null);
    try {
      // The server streams the file (any size); values and headers (column
      // names) are formatted server-side.
      const blob = await api.postBlob(`/lists/${listUuid}/export`, {
        format,
        columns: chosen.map((c) => c.name),
        filters: { conjunction, conditions: cleanConditions(conditions) },
        sorts: sortColumn
          ? [{ column_uuid: sortColumn, direction: sortDir }]
          : [],
        limit,
      });
      downloadBlob(`${listName}.${format}`, blob);
      toast.success("Export ready", `${listName}.${format}`);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Export failed";
      setExportError(message);
      toast.error("Export failed", message);
    } finally {
      setBusy(false);
    }
  }

  // Every writable column, not the export tab's ticked subset: a template answers
  // "what shape does a file for this list have?", and the answer is the whole list
  // minus what you cannot write. Columns you have nothing to say about can be
  // deleted from the file — import only sets the ones it finds.
  function handleTemplate() {
    if (templateFormat === "json") {
      const sample = Object.fromEntries(transferable.map((c) => [c.name, ""]));
      downloadFile(
        `${listName}-template.json`,
        JSON.stringify([sample], null, 2),
        "application/json",
      );
    } else {
      downloadFile(
        `${listName}-template.csv`,
        toCsv(transferable.map((c) => c.name), []),
        "text/csv",
      );
    }
  }

  async function handleImport(event: FileUploadHandlerEvent) {
    const file = event.files?.[0];
    event.options.clear();
    if (!file) return;
    setBusy(true);
    setResult(null);
    try {
      // The server parses and bulk-inserts, validating each row and reporting
      // per-row errors.
      const form = new FormData();
      form.append("file", file);
      form.append(
        "format",
        file.name.toLowerCase().endsWith(".json") ? "json" : "csv",
      );
      const summary = await api.postForm<ImportResult>(
        `/lists/${listUuid}/import`,
        form,
      );
      setResult(summary);
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
      const skipped = summary.errors.length;
      if (skipped === 0) {
        toast.success("Import complete", `${summary.created} record(s) added`);
      } else {
        toast.warn(
          "Import finished with errors",
          `${summary.created} added, ${skipped} skipped`,
        );
      }
    } catch (err) {
      setResult({
        created: 0,
        errors: [
          { row: 0, message: err instanceof ApiError ? err.message : "Import failed" },
        ],
      });
    } finally {
      setBusy(false);
    }
  }

  // Getting data out and putting data in are two jobs, and only one of them is
  // ever the one you opened this for. Stacked, the export half's four sections
  // stood between you and the file picker; as tabs each is a screen with one
  // thing on it, and the footer can carry that screen's action.
  const exporting = tab === 0 || !canImport;

  return (
    <AppDialog
      header="Import / Export"
      visible={visible}
      onHide={onHide}
      style={{ width: 560 }}
      contentStyle={{ padding: 0 }}
      footer={
        exporting ? (
          <Button
            label="Export records"
            icon="pi pi-download"
            onClick={handleExport}
            loading={busy}
            disabled={chosen.length === 0}
          />
        ) : (
          <>
            <Button
              label="Download template"
              icon="pi pi-file"
              text
              onClick={handleTemplate}
              disabled={transferable.length === 0}
            />
            <Button label="Close" text onClick={onHide} />
          </>
        )
      }
    >
      <TabView activeIndex={tab} onTabChange={(e) => setTab(e.index)}>
        <TabPanel header="Export">
          <div className="flex flex-col gap-5">
            {exportError && <Message severity="error" text={exportError} />}

            <FormSection title="Format">
              <SelectButton
                value={format}
                onChange={(e) => e.value && setFormat(e.value)}
                options={[
                  { label: "CSV", value: "csv" },
                  { label: "JSON", value: "json" },
                ]}
              />
            </FormSection>

            <FormSection
              title="Columns"
              description={`Headers use the column name. ${chosen.length} of ${transferable.length} selected.`}
            >
              <div className="flex items-center gap-1 -mt-1">
                <Button
                  label="All"
                  text
                  size="small"
                  onClick={() =>
                    transferable.forEach((c) => toggle(c.uuid, true))
                  }
                />
                <Button
                  label="None"
                  text
                  size="small"
                  onClick={() =>
                    transferable.forEach((c) => toggle(c.uuid, false))
                  }
                />
              </div>
              <div className="flex flex-col gap-1 max-h-48 overflow-auto">
                {transferable.map((col) => (
                  <div key={col.uuid} className="flex items-center gap-2">
                    <Checkbox
                      inputId={`io-${col.uuid}`}
                      checked={selected.has(col.uuid)}
                      onChange={(e) => toggle(col.uuid, !!e.checked)}
                    />
                    <label
                      htmlFor={`io-${col.uuid}`}
                      className="flex-1 cursor-pointer text-sm"
                    >
                      {col.display_name}{" "}
                      <span className="text-muted">({col.name})</span>
                    </label>
                  </div>
                ))}
              </div>
            </FormSection>

            <FormSection title="Which records">
              <FieldRow>
                <Field label="Limit" htmlFor="io-limit" optional>
                  <InputNumber
                    inputId="io-limit"
                    value={limit}
                    onValueChange={(e) => setLimit(e.value ?? null)}
                    min={1}
                    useGrouping={false}
                    placeholder="All"
                  />
                </Field>
                <Field label="Sort by" htmlFor="io-sort" optional>
                  <Dropdown
                    inputId="io-sort"
                    value={sortColumn}
                    options={columns.map((c) => ({
                      label: c.display_name,
                      value: c.uuid,
                    }))}
                    onChange={(e) => setSortColumn(e.value)}
                    placeholder="Record ID"
                    showClear
                    className="w-full"
                  />
                </Field>
                <Field label="Direction">
                  <SelectButton
                    value={sortDir}
                    onChange={(e) => e.value && setSortDir(e.value)}
                    options={[
                      { label: "Asc", value: "asc" },
                      { label: "Desc", value: "desc" },
                    ]}
                    disabled={!sortColumn}
                  />
                </Field>
              </FieldRow>
              <Field label="Filters" optional>
                <FilterConditionsEditor
                  listUuid={listUuid}
                  columns={columns}
                  conjunction={conjunction}
                  onConjunctionChange={setConjunction}
                  conditions={conditions}
                  onConditionsChange={setConditions}
                />
              </Field>
            </FormSection>
          </div>
        </TabPanel>

        {canImport ? (
          <TabPanel header="Import">
            <div className="flex flex-col gap-4">
              <p className="field-hint">
                Upload a .csv or .json file whose headers/keys are column names.
                Unknown columns are ignored; each row is validated on the server.
                Not sure of the shape? Download the template.
              </p>
              <Field
                label="Template format"
                hint="An empty file with one heading per column, ready to fill in."
              >
                <SelectButton
                  value={templateFormat}
                  onChange={(e) => e.value && setTemplateFormat(e.value)}
                  options={[
                    { label: "CSV", value: "csv" },
                    { label: "JSON", value: "json" },
                  ]}
                />
              </Field>
              <FileUpload
                mode="basic"
                customUpload
                auto
                accept=".csv,.json"
                chooseLabel="Choose file"
                uploadHandler={handleImport}
                disabled={busy}
              />
              {result && (
                <Message
                  severity={result.errors.length === 0 ? "success" : "warn"}
                  text={`Imported ${result.created} record(s)${
                    result.errors.length
                      ? `, ${result.errors.length} skipped`
                      : ""
                  }.`}
                />
              )}
              {result && result.errors.length > 0 && (
                <ul className="text-sm text-danger m-0 pl-4 max-h-40 overflow-auto">
                  {result.errors.map((e, i) => (
                    <li key={i}>
                      {e.row ? `Row ${e.row}: ` : ""}
                      {e.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </TabPanel>
        ) : null}
      </TabView>
    </AppDialog>
  );
}
