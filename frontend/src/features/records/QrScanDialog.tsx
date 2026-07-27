import { useEffect, useRef, useState, type ReactNode } from "react";
import { Html5Qrcode, Html5QrcodeSupportedFormats } from "html5-qrcode";
import { Button } from "primereact/button";
import { FileUpload, type FileUploadHandlerEvent } from "primereact/fileupload";
import { Message } from "primereact/message";
import { ProgressSpinner } from "primereact/progressspinner";

import { AppDialog } from "../../components/AppDialog";

interface Props {
  header: string;
  /** What happens with the code once read — shown so the two menu options differ visibly. */
  hint: string;
  /** Whatever the parent is doing with the decoded text (resolving references, submitting). */
  busy: boolean;
  /** A problem with the *payload*, from the parent. Camera problems are handled here. */
  error: string | null;
  onScan: (text: string) => void;
  onHide: () => void;
  /** Keep reading codes after each one, instead of stopping at the first. */
  continuous?: boolean;
  /** The session's own status — a tally, a log — shown under the preview. */
  children?: ReactNode;
  /** "Cancel" reads wrong once codes have already been accepted. */
  closeLabel?: string;
}

const REGION_ID = "qr-scan-region";
/**
 * How long the same code is ignored for in continuous mode.
 *
 * A camera reports a code ten times a second for as long as it stays in frame, so
 * without this one badge held up would submit a dozen records. The window is not a
 * debounce settling on a value: the *first* sighting is what counts and the rest of
 * the visit is dropped. It also has to expire rather than latch forever — the same
 * person scanning in again later is a real second record, not a mistake — so five
 * seconds, long enough to cover holding a badge steady and short enough that a
 * genuine rescan needs no explaining.
 */
const DUPLICATE_WINDOW_MS = 5000;
const DUPLICATE_HINT_MS = 2000;

function messageOf(err: unknown): string {
  if (typeof err === "string") return err;
  if (err instanceof Error) return err.message;
  return "The camera could not be started.";
}

/**
 * Read QR codes, from the camera or from an image.
 *
 * One code by default, and the parent closes the dialog with it. `continuous` is
 * the attendance case: a queue of people, each with a badge, and a scanner that
 * has to stay open across all of them. The difference is only in what counts as a
 * repeat — see `accept` — because a camera cannot tell "still the same badge" from
 * "the next person" on its own.
 *
 * The image fallback is not a nicety: `getUserMedia` needs HTTPS (or localhost)
 * and a camera, so on a desktop — where a good half of this app is used — the
 * camera path simply does not exist, and a scanner that only scans would be a
 * dead button. Either path produces the same thing: the code's text, handed to
 * the parent, which owns what it *means* (see `qrFill.ts`).
 *
 * **Why the promise chain.** html5-qrcode throws if you `start()` a scanner that
 * is already running, or `stop()` one that never started — and StrictMode mounts
 * this effect twice, so the second `start()` races the first one's teardown. Every
 * camera operation is therefore queued on `chain`, which serializes them: the
 * second start waits for the first stop, whatever order React runs them in. The
 * same queue is what lets a file scan borrow the camera's scanner safely.
 */
export function QrScanDialog({
  header,
  hint,
  busy,
  error,
  onScan,
  onHide,
  continuous = false,
  children,
  closeLabel,
}: Props) {
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState(false);
  // The preview's node, captured by a ref callback rather than looked up in a
  // mount effect. PrimeReact's Dialog renders *nothing* on its first commit — it
  // flips its own `maskVisible` state in an effect — so at the moment this
  // component's mount effect would run, the div below is not in the document yet
  // and `new Html5Qrcode(id)` would throw "element not found". A ref callback
  // cannot fire before the node is attached, so this is right whatever the Dialog
  // does with its timing.
  const [region, setRegion] = useState<HTMLDivElement | null>(null);
  const scannerRef = useRef<Html5Qrcode | null>(null);
  // Camera operations, serialized. See the note above.
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  // A camera fires its callback every frame the code stays in view; a one-shot
  // scan is only interested in the first one.
  const handled = useRef(false);
  // Continuous mode's equivalent: when each code was last accepted, so the same
  // one held in frame is dropped rather than submitted again and again.
  const recent = useRef(new Map<string, number>());
  const hintTimer = useRef<number | undefined>(undefined);
  // These are read by `accept`, which the camera captured on the *first* render
  // (see below) — so every value it reads has to come from a ref, or it would be
  // frozen at whatever it was when the camera started.
  const onScanRef = useRef(onScan);
  onScanRef.current = onScan;
  const continuousRef = useRef(continuous);
  continuousRef.current = continuous;
  const busyRef = useRef(busy);
  busyRef.current = busy;

  function accept(text: string) {
    // A code read while the parent is still submitting the last one would race it.
    if (busyRef.current) return;

    if (continuousRef.current) {
      const now = Date.now();
      for (const [seen, at] of recent.current) {
        if (now - at >= DUPLICATE_WINDOW_MS) recent.current.delete(seen);
      }
      if (recent.current.has(text)) {
        // Silence would look like a scanner that had stopped working.
        setDuplicate(true);
        window.clearTimeout(hintTimer.current);
        hintTimer.current = window.setTimeout(
          () => setDuplicate(false),
          DUPLICATE_HINT_MS,
        );
        return;
      }
      recent.current.set(text, now);
      setDuplicate(false);
      onScanRef.current(text);
      return;
    }

    if (handled.current) return;
    handled.current = true;
    onScanRef.current(text);
  }

  useEffect(() => () => window.clearTimeout(hintTimer.current), []);

  function startCamera(scanner: Html5Qrcode) {
    return scanner
      .start(
        { facingMode: "environment" },
        { fps: 10, qrbox: { width: 240, height: 240 } },
        (text) => accept(text),
        // Fires for every frame without a code in it — that is not an error.
        () => {},
      )
      .then(() => setCameraError(null))
      .catch((err) => setCameraError(messageOf(err)));
  }

  useEffect(() => {
    if (!region) return;
    handled.current = false;
    const scanner = new Html5Qrcode(region.id, {
      formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE],
      verbose: false,
    });
    scannerRef.current = scanner;
    chain.current = chain.current
      .catch(() => {})
      .then(() => startCamera(scanner));

    return () => {
      scannerRef.current = null;
      chain.current = chain.current.catch(() => {}).then(async () => {
        try {
          await scanner.stop();
        } catch {
          // Never started (no camera, permission denied): nothing to stop.
        }
        try {
          scanner.clear();
        } catch {
          // Already torn down.
        }
      });
    };
  }, [region]);

  // The parent rejected what we read (not JSON, wrong shape). The camera is still
  // running, so let the next code through — otherwise the first bad scan would
  // wedge the dialog on its error message.
  //
  // Continuous mode is deliberately *not* re-armed here, and must not be: the code
  // that failed is very likely still in front of the camera, so forgetting it would
  // resubmit it ten times a second for as long as it stayed there. Its duplicate
  // window expiring is the re-arm, and the wait costs nothing — a code rejected for
  // what it contains will be rejected again on sight, so the fix is a different code
  // or a record added by hand, never an immediate retry of the same one.
  useEffect(() => {
    if (error) handled.current = false;
  }, [error]);

  function handleFile(event: FileUploadHandlerEvent) {
    const file = event.files?.[0];
    event.options.clear();
    const scanner = scannerRef.current;
    if (!file || !scanner) return;
    setFileError(null);
    chain.current = chain.current.catch(() => {}).then(async () => {
      // scanFile needs the scanner idle, so the camera yields to the image and
      // comes back afterwards if the image had nothing in it.
      try {
        await scanner.stop();
      } catch {
        // Wasn't running.
      }
      try {
        accept(await scanner.scanFile(file, false));
      } catch {
        setFileError("No QR code was found in that image.");
        await startCamera(scanner);
      }
    });
  }

  return (
    <AppDialog header={header} visible onHide={onHide} style={{ width: 460 }}>
      <div className="flex flex-col gap-4">
        <p className="m-0 text-sm" style={{ color: "var(--text-muted)" }}>
          {hint}
        </p>

        {/* html5-qrcode owns this node's contents — it mounts the camera preview
            into it — so React must not render anything inside it. */}
        <div
          id={REGION_ID}
          ref={setRegion}
          className="w-full"
          style={{ minHeight: cameraError ? 0 : 240 }}
        />

        {busy && (
          <div className="flex items-center gap-2">
            <ProgressSpinner
              style={{ width: 24, height: 24 }}
              strokeWidth="4"
            />
            <span>Reading the code…</span>
          </div>
        )}
        {duplicate && !busy && (
          <Message severity="info" text="Already scanned just now." />
        )}
        {error && <Message severity="error" text={error} />}
        {cameraError && (
          <Message
            severity="warn"
            text={`${cameraError} You can still choose an image of the code below.`}
          />
        )}
        {fileError && <Message severity="error" text={fileError} />}

        {children}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <FileUpload
            mode="basic"
            customUpload
            auto
            accept="image/*"
            chooseLabel="Scan an image"
            chooseOptions={{ icon: "pi pi-image" }}
            uploadHandler={handleFile}
            disabled={busy}
          />
          <Button
            label={closeLabel ?? "Cancel"}
            text
            onClick={onHide}
            disabled={busy}
          />
        </div>
      </div>
    </AppDialog>
  );
}
