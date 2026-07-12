import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { SelectButton } from "primereact/selectbutton";
import { Dropdown } from "primereact/dropdown";
import { InputNumber } from "primereact/inputnumber";
import { Divider } from "primereact/divider";
import {
  FileUpload,
  type FileUploadHandlerEvent,
} from "primereact/fileupload";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
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
  const [format, setFormat] = useState<Format>("csv");
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

  // Default to all columns selected whenever the dialog opens.
  useEffect(() => {
    if (visible) {
      setSelected(new Set(columns.map((c) => c.uuid)));
      setResult(null);
      setExportError(null);
    }
  }, [visible, columns]);

  const chosen = columns.filter((c) => selected.has(c.uuid));

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

  function handleTemplate() {
    if (format === "json") {
      const sample = Object.fromEntries(chosen.map((c) => [c.name, ""]));
      downloadFile(
        `${listName}-template.json`,
        JSON.stringify([sample], null, 2),
        "application/json",
      );
    } else {
      downloadFile(
        `${listName}-template.csv`,
        toCsv(chosen.map((c) => c.name), []),
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

  return (
    <Dialog
      header="Import / Export"
      visible={visible}
      onHide={onHide}
      style={{ width: 520 }}
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <span className="font-semibold">Format</span>
          <SelectButton
            value={format}
            onChange={(e) => e.value && setFormat(e.value)}
            options={[
              { label: "CSV", value: "csv" },
              { label: "JSON", value: "json" },
            ]}
          />
        </div>

        <div className="flex flex-col gap-2">
          <span className="font-semibold">Columns</span>
          <p className="text-sm text-gray-500 m-0">
            Headers use the column name. {chosen.length}/{columns.length} selected.
          </p>
          <div className="flex flex-col gap-1 max-h-48 overflow-auto">
            {columns.map((col) => (
              <div key={col.uuid} className="flex items-center gap-2">
                <Checkbox
                  inputId={`io-${col.uuid}`}
                  checked={selected.has(col.uuid)}
                  onChange={(e) => toggle(col.uuid, !!e.checked)}
                />
                <label htmlFor={`io-${col.uuid}`} className="flex-1">
                  {col.display_name}{" "}
                  <span className="text-gray-400">({col.name})</span>
                </label>
              </div>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <span className="font-semibold">Export options</span>
          <div className="flex items-center gap-2 flex-wrap">
            <label htmlFor="io-limit" className="text-sm">
              Limit
            </label>
            <InputNumber
              inputId="io-limit"
              value={limit}
              onValueChange={(e) => setLimit(e.value ?? null)}
              min={1}
              useGrouping={false}
              placeholder="All"
              style={{ width: 90 }}
            />
            <label htmlFor="io-sort" className="text-sm ml-2">
              Sort
            </label>
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
              style={{ width: 160 }}
            />
            <SelectButton
              value={sortDir}
              onChange={(e) => e.value && setSortDir(e.value)}
              options={[
                { label: "Asc", value: "asc" },
                { label: "Desc", value: "desc" },
              ]}
              disabled={!sortColumn}
            />
          </div>
          <span className="text-sm text-gray-500">Filters (optional)</span>
          <FilterConditionsEditor
            columns={columns}
            conjunction={conjunction}
            onConjunctionChange={setConjunction}
            conditions={conditions}
            onConditionsChange={setConditions}
          />
        </div>

        {exportError && <Message severity="error" text={exportError} />}
        <div className="flex gap-2">
          <Button
            label="Export records"
            icon="pi pi-download"
            onClick={handleExport}
            loading={busy}
            disabled={chosen.length === 0}
          />
          <Button
            label="Download template"
            icon="pi pi-file"
            outlined
            onClick={handleTemplate}
            disabled={chosen.length === 0}
          />
        </div>

        {canImport && (
          <>
            <Divider />

            <div className="flex flex-col gap-2">
              <span className="font-semibold">Import records</span>
              <p className="text-sm text-gray-500 m-0">
                Upload a .csv or .json file whose headers/keys are column names.
                Unknown columns are ignored; each row is validated on the server.
              </p>
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
                    result.errors.length ? `, ${result.errors.length} skipped` : ""
                  }.`}
                />
              )}
              {result && result.errors.length > 0 && (
                <ul className="text-sm text-red-600 m-0 pl-4 max-h-40 overflow-auto">
                  {result.errors.map((e, i) => (
                    <li key={i}>
                      {e.row ? `Row ${e.row}: ` : ""}
                      {e.message}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
