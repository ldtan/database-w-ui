/** QR codes for records: one to look at, or a sheet to print.
 *
 *  One dialog for both, rather than two, because the two differ only in how many codes are
 *  laid out — the column picker, the payload request, the size advice and the print path
 *  are all shared, and splitting them would mean keeping two copies of each in step.
 *
 *  **The payload comes from the server** (`POST /tables/{uuid}/records/qr`), which is the whole
 *  reason this feature round-trips: a code's values have to be the ones the importer reads,
 *  and resolving a reference cell to that value needs the database and the caller's
 *  permissions on the target table. Building it from the grid would be a second
 *  implementation of a rule that already has one — and a subtly different one, since a
 *  group shows its display name on screen and exports its slug.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "primereact/button";
import { Dropdown } from "primereact/dropdown";
import { Message } from "primereact/message";
import { ProgressSpinner } from "primereact/progressspinner";

import { AppDialog } from "../../components/AppDialog";
import { FormSection } from "../../components/Field";
import { api } from "../../lib/api";
import { downloadBlob, downloadFile } from "../../lib/download";
import { BULK_MAX } from "../../lib/limits";
import { byteLength, encode, payloadText, type EncodedQr } from "../../lib/qr";
import type { Column, TableRecord, TableRead } from "../../lib/types";
import {
  QrColumnPicker,
  resolveQrSelection,
  type QrSelection,
} from "./QrColumnPicker";

interface QrPayloadRow {
  record_uuid: string;
  payload: Record<string, unknown>;
}

/** Printed code sizes, in millimetres of the code itself.
 *
 *  Millimetres rather than pixels because the output is paper: "25 mm" is a thing somebody
 *  can hold a ruler to, where "96 px" depends on a print dialog's scaling. The small end is
 *  about as small as a phone will read a version-6 code; the large end is a lanyard badge.
 */
const SIZES = [
  { label: "Small — 25 mm", value: 25 },
  { label: "Medium — 35 mm", value: 35 },
  { label: "Large — 50 mm", value: 50 },
];

/** The class the print stylesheet keys off. See `index.css`. */
const PRINTING = "qr-printing";

export function QrCodeDialog({
  tableUuid,
  lst,
  columns,
  items,
  canConfigure,
  visible,
  onHide,
}: {
  tableUuid: string;
  lst: TableRead | undefined;
  columns: Column[];
  /** One record, or the selection. The sheet layout kicks in past one. */
  items: TableRecord[];
  /** `configure` on the table — gates *saving* the field choice, not making it. */
  canConfigure: boolean;
  visible: boolean;
  onHide: () => void;
}) {
  const [selection, setSelection] = useState<QrSelection>({
    columns: [],
    includeUuid: true,
  });
  const [size, setSize] = useState(35);
  const [showPayload, setShowPayload] = useState(false);
  // Whether the user has touched the ticks yet. Without it, the seeding effect below
  // re-seeds from the table every time react-query hands back a new object and silently
  // undoes what they just chose — the same guard, for the same reason, as
  // `ImportExportDialog`'s `chosenByUser`.
  const chosenByUser = useRef(false);
  // The printable subtree, so `print` clones the node itself rather than
  // re-finding it by selector.
  const printRef = useRef<HTMLDivElement>(null);
  // Whether `selection` reflects the table yet. The query below waits for it: without the
  // gate it fires once on the initial empty selection — a request whose answer is thrown
  // away, and a code that flashes up carrying the wrong fields — and then again once the
  // saved choice arrives.
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    if (!visible) {
      chosenByUser.current = false;
      setSeeded(false);
      return;
    }
    if (chosenByUser.current) return;
    setSelection(resolveQrSelection(lst, columns));
    setSeeded(true);
  }, [visible, lst, columns]);

  const uuids = useMemo(() => items.map((i) => i.uuid), [items]);
  const many = uuids.length > 1;

  const payloads = useQuery({
    queryKey: ["qr-payloads", tableUuid, uuids, selection],
    queryFn: async () => {
      // Chunked against the server's own cap, like every other bulk call in the app
      // (`BULK_MAX` mirrors `BULK_DELETE_MAX`). A selection past it is rare and a refusal
      // would be the wrong answer to it.
      const out: QrPayloadRow[] = [];
      for (let i = 0; i < uuids.length; i += BULK_MAX) {
        out.push(
          ...(await api.post<QrPayloadRow[]>(`/tables/${tableUuid}/records/qr`, {
            record_uuids: uuids.slice(i, i + BULK_MAX),
            columns: selection.columns,
            include_uuid: selection.includeUuid,
          })),
        );
      }
      return out;
    },
    enabled: visible && seeded && uuids.length > 0,
  });

  // Encoding is async (the library is dynamically imported) and there may be a sheet of
  // them, so the codes are state rather than something rendered inline.
  const [codes, setCodes] = useState<Map<string, EncodedQr>>(new Map());
  const [encodeError, setEncodeError] = useState<string | null>(null);

  useEffect(() => {
    const rows = payloads.data;
    if (!rows) return;
    let cancelled = false;
    (async () => {
      try {
        const next = new Map<string, EncodedQr>();
        for (const row of rows) {
          next.set(row.record_uuid, await encode(payloadText(row.payload)));
        }
        if (!cancelled) {
          setCodes(next);
          setEncodeError(null);
        }
      } catch (err) {
        // One over-long payload fails the batch, deliberately: every code on a sheet
        // carries the same fields, so if one record's values do not fit then the choice of
        // fields is wrong for this table rather than for that record. Showing 39 codes and
        // silently omitting the 40th is how somebody hands out a badge that does not exist.
        if (!cancelled) {
          setCodes(new Map());
          setEncodeError(err instanceof Error ? err.message : "Could not encode");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [payloads.data]);

  const rows = payloads.data ?? [];
  const first = rows[0];
  const firstCode = first ? codes.get(first.record_uuid) : undefined;
  const dense = [...codes.values()].some((c) => c.dense);

  /** The size readout, measured on the **worst** code in the batch rather than the first.
   *
   *  Records differ in length — a long name is a denser code — so on a sheet the first
   *  one's numbers describe one badge and imply all of them. The one that governs whether
   *  this batch is printable is the largest, and it is the one worth reporting. */
  const worst = rows.reduce(
    (acc, row) => {
      const bytes = byteLength(payloadText(row.payload));
      const version = codes.get(row.record_uuid)?.version ?? 0;
      return bytes > acc.bytes ? { bytes, version } : acc;
    },
    { bytes: 0, version: 0 },
  );

  /** The caption under a code on the sheet: the first selected column's value, falling
   *  back to the record id. Somebody handing out badges needs to tell them apart, and the
   *  code itself is not readable by eye. */
  function caption(row: QrPayloadRow): string {
    for (const name of selection.columns) {
      const value = row.payload[name];
      if (value !== null && value !== undefined && value !== "") return String(value);
    }
    return row.record_uuid.slice(0, 8);
  }

  /** A caption as a filename. A record's value is arbitrary text and a download name is
   *  not: a slash reads as a path on some platforms and a colon is illegal on Windows. */
  function fileName(row: QrPayloadRow, extension: string): string {
    const safe = caption(row).replace(/[/\\?%*:|"<>]/g, "-").trim().slice(0, 80);
    return `${safe || "qr-code"}.${extension}`;
  }

  function print() {
    // **Copy the codes to a top-level container, then hide everything else by `display`.**
    //
    // The tidier-looking version of this — leave the sheet where it is and use
    // `visibility: hidden` on the app, `visible` on the subtree — was tried and is
    // broken: `visibility` is the one property a descendant can turn back on, which means
    // it is also the one a component library can. PrimeReact sets it on
    // `.p-button-label`, so every button's text printed straight through its hidden
    // parent and the page came out with "Settings", "Add record" and "Delete selected"
    // scattered around the codes.
    //
    // `display: none` cannot be overridden from inside, but it also takes the whole
    // subtree with it — so the content has to be a *sibling* of what is hidden rather
    // than nested in a dialog that is itself hidden. Hence the clone.
    //
    // A clone rather than a move: the originals stay mounted and React keeps owning them,
    // and there is nothing interactive to lose — the codes are `<img>` tags with data:
    // URLs and a caption, all of which survive `cloneNode` intact (including the inline
    // `--qr-size`).
    const source = printRef.current;
    if (!source) return;

    const host = document.createElement("div");
    host.id = "qr-print-root";
    host.appendChild(source.cloneNode(true));
    document.body.appendChild(host);
    document.documentElement.classList.add(PRINTING);

    const clear = () => {
      document.documentElement.classList.remove(PRINTING);
      host.remove();
    };
    // `afterprint` is the reliable signal; the timeout is the fallback for a browser that
    // never fires it, because leaving the class on would blank the app.
    window.addEventListener("afterprint", clear, { once: true });
    window.setTimeout(clear, 60_000);
    window.print();
  }

  const busy =
    !seeded ||
    payloads.isPending ||
    (rows.length > 0 && codes.size === 0 && !encodeError);

  return (
    <AppDialog
      header={many ? `QR codes — ${uuids.length} records` : "QR code"}
      visible={visible}
      onHide={onHide}
      style={{ width: many ? 720 : 520 }}
      footer={
        <>
          <Button label="Close" text onClick={onHide} />
          {!many && firstCode && (
            <>
              <Button
                label="PNG"
                icon="pi pi-download"
                text
                onClick={async () => {
                  const blob = await (await fetch(firstCode.dataUrl)).blob();
                  downloadBlob(fileName(first, "png"), blob);
                }}
              />
              <Button
                label="SVG"
                icon="pi pi-download"
                text
                onClick={() =>
                  downloadFile(fileName(first, "svg"), firstCode.svg, "image/svg+xml")
                }
              />
            </>
          )}
          <Button
            label="Print"
            icon="pi pi-print"
            disabled={codes.size === 0}
            onClick={print}
          />
        </>
      }
    >
      {payloads.isError && (
        <Message severity="error" text="Could not build the codes' contents." />
      )}
      {encodeError && <Message severity="error" text={encodeError} />}

      {busy && (
        <div className="flex justify-center p-4">
          <ProgressSpinner style={{ width: 36, height: 36 }} />
        </div>
      )}

      {!busy && !payloads.isError && rows.length === 0 && (
        <Message
          severity="info"
          text="Nothing to make a code from — these records are no longer visible."
        />
      )}

      {/* The printable subtree. Everything else in the document is hidden while printing.
          The chosen size is handed to CSS as a custom property rather than set inline,
          because the screen and the paper want different sizes from the same number: on
          paper it is exactly the millimetres asked for, while on screen a single code is
          drawn large enough to scan off the monitor (which 35 mm, about 132 px, is not).
          An inline width would win over both. */}
      {codes.size > 0 && (
        <div
          ref={printRef}
          className={many ? "qr-sheet" : "qr-single"}
          data-qr-print
          style={{ "--qr-size": `${size}mm` } as React.CSSProperties}
        >
          {rows.map((row) => {
            const code = codes.get(row.record_uuid);
            if (!code) return null;
            return (
              <figure key={row.record_uuid} className="qr-cell">
                <img src={code.dataUrl} alt={`QR code for ${caption(row)}`} />
                <figcaption className="qr-caption">{caption(row)}</figcaption>
              </figure>
            );
          })}
        </div>
      )}

      {dense && (
        <Message
          severity="warn"
          text="These codes are dense. Print them at the large size, or take a field off — a crowded code is hard for a phone to read off paper."
        />
      )}

      {/* Nothing below here prints: the stylesheet keeps only [data-qr-print]. */}
      <div className="qr-controls">
        <Dropdown
          value={size}
          options={SIZES}
          onChange={(e) => setSize(e.value)}
          aria-label="Printed size"
        />
        {firstCode && (
          <span className="text-sm text-muted">
            {many ? "largest: " : ""}
            {worst.bytes} bytes · version {worst.version}
          </span>
        )}
      </div>

      <FormSection
        title="Fields on the code"
        description="Each code holds these as JSON. Fewer fields make a code easier to scan."
      >
        <QrColumnPicker
          tableUuid={tableUuid}
          columns={columns}
          selection={selection}
          onChange={(next) => {
            chosenByUser.current = true;
            setSelection(next);
          }}
          canSave={canConfigure}
        />
      </FormSection>

      {first && (
        <>
          <Button
            // Named for what it shows: on a sheet it is the *first* code's contents, not
            // every code's, and every code carries the same fields anyway — so one is the
            // useful sample rather than a partial answer.
            label={
              showPayload
                ? "Hide contents"
                : many
                  ? "Show the first code's contents"
                  : "Show contents"
            }
            icon={showPayload ? "pi pi-chevron-down" : "pi pi-chevron-right"}
            text
            size="small"
            onClick={() => setShowPayload((v) => !v)}
          />
          {showPayload && (
            <pre className="qr-payload">
              {JSON.stringify(first.payload, null, 2)}
            </pre>
          )}
        </>
      )}
    </AppDialog>
  );
}
