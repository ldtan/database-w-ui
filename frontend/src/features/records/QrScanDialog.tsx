import { useEffect, useRef, useState } from "react";
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
}

const REGION_ID = "qr-scan-region";

function messageOf(err: unknown): string {
  if (typeof err === "string") return err;
  if (err instanceof Error) return err.message;
  return "The camera could not be started.";
}

/**
 * Read one QR code, from the camera or from an image.
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
}: Props) {
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
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
  // A camera fires its callback every frame the code stays in view; the parent is
  // only interested in the first one.
  const handled = useRef(false);
  // Kept in a ref so the effect below does not restart the camera on every render.
  const onScanRef = useRef(onScan);
  onScanRef.current = onScan;

  function accept(text: string) {
    if (handled.current) return;
    handled.current = true;
    onScanRef.current(text);
  }

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
        {error && <Message severity="error" text={error} />}
        {cameraError && (
          <Message
            severity="warn"
            text={`${cameraError} You can still choose an image of the code below.`}
          />
        )}
        {fileError && <Message severity="error" text={fileError} />}

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
          <Button label="Cancel" text onClick={onHide} disabled={busy} />
        </div>
      </div>
    </AppDialog>
  );
}
