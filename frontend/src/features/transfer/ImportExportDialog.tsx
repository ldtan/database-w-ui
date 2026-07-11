import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog } from "primereact/dialog";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { SelectButton } from "primereact/selectbutton";
import { Divider } from "primereact/divider";
import {
  FileUpload,
  type FileUploadHandlerEvent,
} from "primereact/fileupload";
import { Message } from "primereact/message";

import { api, ApiError } from "../../lib/api";
import { toCsv, parseCsv } from "../../lib/csv";
import { downloadFile } from "../../lib/download";
import type { Column, ListItem } from "../../lib/types";

interface Props {
  listUuid: string;
  listName: string;
  columns: Column[];
  visible: boolean;
  onHide: () => void;
}

type Format = "csv" | "json";

interface ImportResult {
  created: number;
  errors: { row: number; message: string }[];
}

// -- value <-> file conversions ------------------------------------------

// For CSV cells: everything becomes a string. Arrays (multi choice/lookup)
// join with ";" — safe because their members are option ids / row uuids.
function toCsvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(";");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

// Turn a raw file value (string from CSV, native from JSON) into the typed
// value the API expects for this column. Returns undefined to skip the cell.
function parseCell(column: Column, raw: unknown): unknown {
  if (raw === null || raw === undefined) return undefined;
  if (typeof raw === "string" && raw.trim() === "") return undefined;
  const multiple = !!column.config.multiple;
  switch (column.type) {
    case "number": {
      const n = typeof raw === "number" ? raw : Number(String(raw).trim());
      if (Number.isNaN(n)) {
        throw new Error(`column "${column.name}": "${raw}" is not a number`);
      }
      return n;
    }
    case "checkbox": {
      if (typeof raw === "boolean") return raw;
      const s = String(raw).trim().toLowerCase();
      if (["true", "1", "yes", "y"].includes(s)) return true;
      if (["false", "0", "no", "n"].includes(s)) return false;
      throw new Error(`column "${column.name}": "${raw}" is not true/false`);
    }
    case "choice":
    case "lookup":
      if (multiple) {
        if (Array.isArray(raw)) return raw;
        return String(raw)
          .split(";")
          .map((s) => s.trim())
          .filter(Boolean);
      }
      return String(raw);
    default:
      return typeof raw === "string" ? raw : String(raw);
  }
}

async function fetchAllItems(listUuid: string): Promise<ListItem[]> {
  const all: ListItem[] = [];
  const limit = 1000;
  for (let skip = 0; ; skip += limit) {
    const batch = await api.get<ListItem[]>(
      `/lists/${listUuid}/items/?skip=${skip}&limit=${limit}`,
    );
    all.push(...batch);
    if (batch.length < limit) break;
  }
  return all;
}

export function ImportExportDialog({
  listUuid,
  listName,
  columns,
  visible,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const [format, setFormat] = useState<Format>("csv");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);

  // Default to all columns selected whenever the dialog opens.
  useEffect(() => {
    if (visible) {
      setSelected(new Set(columns.map((c) => c.uuid)));
      setResult(null);
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
    try {
      const items = await fetchAllItems(listUuid);
      if (format === "json") {
        const rows = items.map((it) =>
          Object.fromEntries(chosen.map((c) => [c.name, it.data?.[c.uuid] ?? null])),
        );
        downloadFile(
          `${listName}.json`,
          JSON.stringify(rows, null, 2),
          "application/json",
        );
      } else {
        const rows = items.map((it) =>
          chosen.map((c) => toCsvCell(it.data?.[c.uuid])),
        );
        downloadFile(
          `${listName}.csv`,
          toCsv(chosen.map((c) => c.name), rows),
          "text/csv",
        );
      }
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
      const text = await file.text();
      const isJson = file.name.toLowerCase().endsWith(".json");
      let rows: Record<string, unknown>[];
      if (isJson) {
        const parsed = JSON.parse(text);
        rows = Array.isArray(parsed) ? parsed : [parsed];
      } else {
        const grid = parseCsv(text).filter((r) =>
          r.some((cell) => cell.trim() !== ""),
        );
        const header = grid.shift() ?? [];
        rows = grid.map((r) =>
          Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])),
        );
      }

      const byName = new Map(columns.map((c) => [c.name, c]));
      const errors: ImportResult["errors"] = [];
      let created = 0;
      const lineBase = isJson ? 1 : 2; // CSV row 1 is the header
      for (let idx = 0; idx < rows.length; idx++) {
        try {
          const data: Record<string, unknown> = {};
          for (const [key, val] of Object.entries(rows[idx])) {
            const col = byName.get(key);
            if (!col) continue; // ignore columns not on this list
            const parsed = parseCell(col, val);
            if (parsed !== undefined) data[col.uuid] = parsed;
          }
          await api.post(`/lists/${listUuid}/items/`, { data });
          created += 1;
        } catch (err) {
          errors.push({
            row: idx + lineBase,
            message: err instanceof ApiError ? err.message : String(err),
          });
        }
      }
      setResult({ created, errors });
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    } catch (err) {
      setResult({
        created: 0,
        errors: [
          {
            row: 0,
            message:
              err instanceof Error ? err.message : "Could not read the file",
          },
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
      </div>
    </Dialog>
  );
}
