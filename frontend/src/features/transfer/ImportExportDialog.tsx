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
import type {
  Column,
  FilterCondition,
  FilterGroup,
  ImportSummary,
  ViewSort,
} from "../../lib/types";
import { FilterConditionsEditor } from "../views/FilterConditionsEditor";
import { cleanConditions } from "../views/operators";
import { importErrorsCsv } from "./importErrors";

interface Props {
  listUuid: string;
  listName: string;
  columns: Column[];
  // Import writes records; export only reads them.
  canImport: boolean;
  // The filter and sort the grid is reading through right now — the page's, which
  // is the view's plus anything asked of it since. Copied into the export's own
  // fields on request; see `applyCurrent` below.
  currentFilters: FilterGroup;
  currentSorts: ViewSort[];
  // The search box's term, if any. Not a filter and not sendable as one, so it is
  // named rather than applied — see the hint under the button.
  currentSearch: string;
  visible: boolean;
  onHide: () => void;
}

type Format = "csv" | "json";

// How many skipped rows are listed in the dialog. Past this the list stops being
// something you read and starts being something you scroll — which is what the
// downloaded file is for.
const ERRORS_SHOWN = 20;

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
 * Filter dialog uses), and "Use current filters and sort" fills those fields in from
 * what the grid is showing — so "export what I am looking at" is one click, and the
 * conditions it applied are still on screen to read and to edit. Import is offered
 * only with `create` **and** `edit` on the list, since a file can do both; it
 * reports per-row errors rather than failing the whole file, and offers the skipped
 * rows back as a file to correct and re-upload (see `importErrors.ts`).
 *
 * Calculated columns are exported and sorted like any other; only the template
 * leaves them out — see `writable` below.
 */
export function ImportExportDialog({
  listUuid,
  listName,
  columns,
  canImport,
  currentFilters,
  currentSorts,
  currentSearch,
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
  const [result, setResult] = useState<ImportSummary | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  // Export query options.
  const [limit, setLimit] = useState<number | null>(null); // null = all rows
  const [sortColumn, setSortColumn] = useState<string | null>(null); // null = id
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [conjunction, setConjunction] = useState<"and" | "or">("and");
  const [conditions, setConditions] = useState<FilterCondition[]>([]);

  // A file you *read* and a file you *write* want different column sets, and a
  // calculated column is the whole of the difference.
  //
  // Exporting one is right: it holds a real value, and a spreadsheet of orders
  // without the line totals is not the export anyone asked for. It sorts and filters
  // too, because the value is stored where the server can order by it.
  //
  // Putting one in a **template** is not. A template is a file to fill in, and
  // import discards any value given for a calculated column — the formula decides it
  // from the row's own cells. A heading whose column is ignored is a heading that
  // looks like it works.
  const writable = useMemo(
    () => columns.filter((c) => c.type !== "calculated"),
    [columns],
  );

  // Default to all columns selected whenever the dialog opens.
  useEffect(() => {
    if (visible) {
      setSelected(new Set(columns.map((c) => c.uuid)));
      setResult(null);
      setExportError(null);
    }
  }, [visible, columns]);

  const chosen = columns.filter((c) => selected.has(c.uuid));

  // What "current" is, in the words the fields below use — so the button says what
  // it is about to do rather than leaving you to press it and compare.
  const currentSummary = useMemo(() => {
    const count = currentFilters.conditions?.length ?? 0;
    const sort = currentSorts[0];
    const sortColumn = sort && columns.find((c) => c.uuid === sort.column_uuid);
    return [
      count === 0 ? "no filter" : `${count} filter${count === 1 ? "" : "s"}`,
      sortColumn
        ? `sorted by ${sortColumn.display_name} (${sort.direction === "asc" ? "ascending" : "descending"})`
        : "sorted by record ID",
    ].join(", ");
  }, [currentFilters, currentSorts, columns]);

  /**
   * Copy the grid's filter and sort into the export's own fields.
   *
   * Copied rather than sent as a mode, because the fields are right there: what the
   * export will do stays visible, and "the current filter plus one more condition"
   * is then an ordinary edit instead of a second feature. Conditions are cloned, so
   * editing one here cannot reach back into the page's filter.
   *
   * The grouping is deliberately not carried across. It orders the grid — group
   * column first, sort within each group — but an export has one sort and no
   * subheaders to keep records under, so copying it would change the order of the
   * file to something the screen never showed.
   */
  function applyCurrent() {
    setConjunction(currentFilters.conjunction ?? "and");
    setConditions((currentFilters.conditions ?? []).map((c) => ({ ...c })));
    const sort = currentSorts[0];
    setSortColumn(sort?.column_uuid ?? null);
    setSortDir(sort?.direction === "desc" ? "desc" : "asc");
  }

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
  // minus what you cannot write — which is where `writable` differs from the export
  // half. Columns you have nothing to say about can be deleted from the file —
  // import only sets the ones it finds.
  function handleTemplate() {
    if (templateFormat === "json") {
      const sample = Object.fromEntries(writable.map((c) => [c.name, ""]));
      downloadFile(
        `${listName}-template.json`,
        JSON.stringify([sample], null, 2),
        "application/json",
      );
    } else {
      downloadFile(
        `${listName}-template.csv`,
        toCsv(writable.map((c) => c.name), []),
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
      const summary = await api.postForm<ImportSummary>(
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
          {
            row: 0,
            message: err instanceof ApiError ? err.message : "Import failed",
            // Nothing was parsed, so there are no cells to hand back — which is
            // also what keeps the download button off a failure like this one.
            values: {},
          },
        ],
      });
    } finally {
      setBusy(false);
    }
  }

  /**
   * The skipped rows, as a file to correct and upload again.
   *
   * Built from the summary rather than asked of the server, like the batch user
   * importer's credentials file: the response already carries every failed row, and
   * a second endpoint would have to hold the import's result somewhere to answer.
   *
   * Built once rather than per render, and null when there is nothing to download —
   * which is also what the button's presence keys off, so the check cannot disagree
   * with what the click produces.
   */
  const errorsCsv = useMemo(
    () => (result ? importErrorsCsv(result.errors) : null),
    [result],
  );

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
              disabled={writable.length === 0}
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
              description={`Headers use the column name. ${chosen.length} of ${columns.length} selected.`}
            >
              <div className="flex items-center gap-1 -mt-1">
                <Button
                  label="All"
                  text
                  size="small"
                  onClick={() =>
                    columns.forEach((c) => toggle(c.uuid, true))
                  }
                />
                <Button
                  label="None"
                  text
                  size="small"
                  onClick={() =>
                    columns.forEach((c) => toggle(c.uuid, false))
                  }
                />
              </div>
              <div className="flex flex-col gap-1 max-h-48 overflow-auto">
                {columns.map((col) => (
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
              <Field
                hint={
                  <>
                    The grid is showing {currentSummary}.
                    {currentSearch.trim() && (
                      <>
                        {" "}
                        Its search for “{currentSearch.trim()}” is not part of an
                        export, so the file will hold the rows that search is
                        hiding too.
                      </>
                    )}
                  </>
                }
              >
                <Button
                  label="Use current filters and sort"
                  icon="pi pi-filter"
                  outlined
                  size="small"
                  onClick={applyCurrent}
                />
              </Field>
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
                <div className="flex flex-col gap-2">
                  <ul className="text-sm text-danger m-0 pl-4 max-h-40 overflow-auto">
                    {result.errors.slice(0, ERRORS_SHOWN).map((e, i) => (
                      <li key={i}>
                        {e.row ? `Row ${e.row}: ` : ""}
                        {e.message}
                      </li>
                    ))}
                  </ul>
                  {result.errors.length > ERRORS_SHOWN && (
                    <p className="field-hint m-0">
                      and {result.errors.length - ERRORS_SHOWN} more — the file
                      below has every one.
                    </p>
                  )}
                  {/* Absent when no failure carried its cells: an upload that never
                      parsed has nothing to put in a file. */}
                  {errorsCsv && (
                    <Field hint="The skipped rows, as you uploaded them, with the reason in an _error column. Fix the cells and import this file — the extra columns are ignored.">
                      <Button
                        label="Download skipped rows"
                        icon="pi pi-download"
                        outlined
                        size="small"
                        onClick={() =>
                          downloadFile(
                            `${listName}-import-errors.csv`,
                            errorsCsv,
                            "text/csv",
                          )
                        }
                      />
                    </Field>
                  )}
                </div>
              )}
            </div>
          </TabPanel>
        ) : null}
      </TabView>
    </AppDialog>
  );
}
