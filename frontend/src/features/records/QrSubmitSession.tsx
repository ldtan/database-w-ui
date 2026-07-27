/**
 * Submitting records by scanning, a queue of them at a time.
 *
 * The shape of this comes from what the feature is actually for: attendance. A
 * badge's QR says who someone is; it cannot say which event they are turning up
 * to, because the badge was printed before the event existed and will be used at
 * the next one too. So a session is two steps — the values that hold for *every*
 * scan are set once, by the person running the door, and then each code supplies
 * only what is particular to it.
 *
 * **A code outranks a preset.** Where both have a value for a field, the scanned
 * one wins: the presets are what to assume in the absence of an answer, and a code
 * that troubled to carry one has answered. This falls out of the merge order
 * rather than being enforced, because `fillFromQr` already drops the keys a code
 * left blank — a code cannot accidentally erase a preset by carrying `""`.
 *
 * **A failed scan does not stop the queue.** The one-at-a-time flow can afford to
 * drop the operator into the record form to fix a rejection by hand; this one
 * cannot, because there are people waiting behind the one whose code was bad. The
 * failure is reported in the log, the camera stays live, and the next person scans.
 * The refused record is added by hand afterwards — rescanning would not help, since
 * a code rejected for what it contains is rejected again on sight.
 *
 * Validation is still the server's alone — see `qrFill.ts`. Nothing here inspects
 * a value; it merges, posts, and reports what came back.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "primereact/button";

import { AppDialog } from "../../components/AppDialog";
import { api, ApiError } from "../../lib/api";
import { useToast } from "../../lib/toast";
import type { Column, ListItem } from "../../lib/types";
import { QrScanDialog } from "./QrScanDialog";
import { RecordFields } from "./RecordFields";
import { fillFromQr, parseQrPayload } from "./qrFill";
import {
  buildRecordData,
  isEmptyValue,
  nestedDescendants,
  serialize,
  type ResolvedField,
} from "./recordValues";

interface Props {
  listUuid: string;
  columns: Column[];
  /** The *create* form's fields — what a preset or a code may write, and no more. */
  fields: ResolvedField[];
  presets: Record<string, unknown>;
  onPresetsChange: (values: Record<string, unknown>) => void;
  onHide: () => void;
}

/** One scan, as the log shows it. */
interface Entry {
  ok: boolean;
  summary: string;
  detail?: string;
  at: string;
}

/** How many scans the log keeps. Older ones are in the table behind the dialog. */
const LOG_LIMIT = 8;

/**
 * A scan, in a few words: enough for the operator to recognise the person who
 * just went past, not a record of what was saved (the table has that).
 *
 * Built from the code's **own** values rather than the filled form's, because by
 * that point a reference is a uuid — "Ana Cruz" has become the thing that
 * identifies her to the database, which is exactly what a human cannot read.
 */
function summarize(payload: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const value of Object.values(payload)) {
    if (isEmptyValue(value)) continue;
    parts.push(Array.isArray(value) ? value.join(", ") : String(value));
    if (parts.length === 2) break;
  }
  const text = parts.join(" · ");
  if (!text) return "(no values)";
  return text.length > 60 ? `${text.slice(0, 59)}…` : text;
}

/** A preset as the scanner's hint names it, avoiding uuids a person can't read. */
function describePreset(field: ResolvedField, value: unknown): string {
  const { type } = field.column;
  if (type === "lookup" || type === "user" || type === "group") {
    return field.label; // a uuid here; the label alone is the honest summary
  }
  if (type === "checkbox") return `${field.label}: ${value ? "yes" : "no"}`;
  return `${field.label}: ${String(serialize(field.column, value))}`;
}

export function QrSubmitSession({
  listUuid,
  columns,
  fields,
  presets,
  onPresetsChange,
  onHide,
}: Props) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [added, setAdded] = useState(0);

  const submit = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.post<ListItem>(`/lists/${listUuid}/items/`, {
        data: buildRecordData(fields, values, false),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["items", listUuid] });
    },
    // No toast per scan: at a scan every few seconds it would be a wall of them,
    // and it would cover the camera. The log below the preview is the feedback.
  });

  const presetNotes = fields
    .filter((field) => !isEmptyValue(presets[field.column.uuid]))
    .map((field) => describePreset(field, presets[field.column.uuid]));

  function log(entry: Entry) {
    setEntries((prev) => [entry, ...prev].slice(0, LOG_LIMIT));
  }

  async function handleScan(text: string) {
    setBusy(true);
    setError(null);
    try {
      const payload = parseQrPayload(text);
      const fill = await fillFromQr(listUuid, fields, payload);
      if (fill.filled.length === 0 && fill.issues.length === 0) {
        setError("This QR code has no fields for this list.");
        return;
      }

      // The presets under the code, so anything the code carried wins.
      const values = { ...presets, ...fill.values };
      // A code that set a parent lookup invalidates any preset chosen under the
      // old parent — the same rule the form applies when a parent is edited.
      for (const uuid of Object.keys(fill.values)) {
        for (const descendant of nestedDescendants(columns, uuid)) {
          if (!(descendant in fill.values)) delete values[descendant];
        }
      }

      const notes = fill.issues.map(
        (issue) => `${issue.key}: ${issue.message}`,
      );
      try {
        await submit.mutateAsync(values);
        setAdded((n) => n + 1);
        log({
          ok: true,
          summary: summarize(payload),
          detail: notes.length > 0 ? notes.join("; ") : undefined,
          at: new Date().toLocaleTimeString(),
        });
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : "The server rejected the record.";
        setError(message);
        log({
          ok: false,
          summary: summarize(payload),
          detail: message,
          at: new Date().toLocaleTimeString(),
        });
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not read that QR code.",
      );
    } finally {
      setBusy(false);
    }
  }

  function finish() {
    if (added > 0) {
      toast.success(
        `${added} record${added === 1 ? "" : "s"} added`,
        "Submitted from QR codes",
      );
    }
    onHide();
  }

  if (!scanning) {
    return (
      <AppDialog
        header="Values for every scan"
        visible
        onHide={onHide}
        style={{ width: 480 }}
        footer={
          <>
            <Button label="Cancel" text onClick={onHide} />
            <Button
              label="Start scanning"
              icon="pi pi-qrcode"
              onClick={() => setScanning(true)}
              disabled={fields.length === 0}
            />
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <p className="field-hint">
            Set what every record in this batch shares — the event, say. Leave the
            rest blank: each QR code fills those, and overrides anything set here
            that it also carries.
          </p>
          {fields.length === 0 && (
            <p className="field-hint">Add a column first.</p>
          )}
          <RecordFields
            listUuid={listUuid}
            fields={fields}
            columns={columns}
            values={presets}
            onChange={onPresetsChange}
          />
        </div>
      </AppDialog>
    );
  }

  return (
    <QrScanDialog
      header="Scan to submit"
      hint={
        presetNotes.length > 0
          ? `Every code adds a record. On all of them — ${presetNotes.join(" · ")}.`
          : "Every code adds a record. The scanner stays open until you are done."
      }
      busy={busy}
      error={error}
      onScan={handleScan}
      onHide={finish}
      continuous
      closeLabel={added > 0 ? "Done" : "Cancel"}
    >
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="font-semibold">Submitted: {added}</span>
          <Button
            label="Change values"
            text
            size="small"
            onClick={() => setScanning(false)}
            disabled={busy}
          />
        </div>
        {entries.length === 0 ? (
          <span className="text-sm" style={{ color: "var(--text-muted)" }}>
            Nothing scanned yet.
          </span>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
            {entries.map((entry, i) => (
              <li key={i} className="flex items-baseline gap-2">
                <i
                  className={entry.ok ? "pi pi-check" : "pi pi-times"}
                  // The app's own tokens, not PrimeReact's --green-500/--red-500:
                  // those are frozen light-mode values from the base theme.
                  style={{ color: entry.ok ? "var(--success)" : "var(--danger)" }}
                />
                <span className="flex-1">
                  {entry.summary}
                  {entry.detail && (
                    <span
                      className="block text-xs"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {entry.detail}
                    </span>
                  )}
                </span>
                <span style={{ color: "var(--text-muted)" }}>{entry.at}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </QrScanDialog>
  );
}
