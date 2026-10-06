/** Turning a record's payload into a QR code.
 *
 *  The payload itself is built by the **server** (`POST /lists/{uuid}/items/qr`), not here,
 *  and that split is deliberate: a generated code is meant to be scannable back by "Fill
 *  using QR code", which means its values have to be the ones the importer reads — and
 *  resolving a reference cell to that value needs the database and the caller's permissions
 *  on the target list. See `transfer/services.py::json_row`, which the export shares.
 *
 *  So this module does the half that is genuinely the browser's: encoding, measuring, and
 *  saying when a payload has grown past what one code can usefully carry.
 */

/** The key the record's own uuid arrives under. Mirrors `QR_UUID_KEY` in
 *  `backend/src/lists/transfer/services.py` — the underscore is what makes it impossible
 *  for a column named `uuid` to collide with it, since a column name cannot start with one. */
export const QR_UUID_KEY = "_uuid";

/** A payload as the bytes the code will hold.
 *
 *  Key order is the server's (`_uuid` first, then the columns in list order) and
 *  `JSON.stringify` preserves it, so what a person reads in the dialog is the order the
 *  code encodes. No pretty-printing: whitespace is bytes, and bytes are modules. */
export function payloadText(payload: Record<string, unknown>): string {
  return JSON.stringify(payload);
}

/** The payload's size in bytes, which is what the code's capacity is measured in.
 *
 *  `string.length` would be wrong and plausibly so: it counts UTF-16 units, so "Ana Cruz"
 *  agrees with it and "Ana Cruz · Café" does not. A name with an accent in it is the
 *  ordinary case here, not the exotic one. */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Version past which a code is dense enough to be worth warning about.
 *
 *  Not the capacity limit — that is ~2,331 bytes at this error-correction level, and
 *  `encode` reports it as an error when it is hit. This is the *earlier* and more useful
 *  line: a QR code's module count grows with its version (version 1 is 21x21, version 40
 *  is 177x177), and at a badge's physical size the modules eventually get smaller than a
 *  phone camera can resolve. Version 16 is 81x81, which is about where a code printed at
 *  3 cm stops being reliable across cheap cameras.
 *
 *  So the advice fires long before the failure, because by the time encoding *fails* the
 *  user has a code that would not have scanned anyway. */
export const DENSE_VERSION_FROM = 16;

/** Error correction level. `M` recovers ~15% damage and is the usual choice for print —
 *  a badge gets creased and thumbed. `L` would buy capacity we would rather not spend on
 *  a payload this small, and `Q`/`H` make the code denser for robustness a laminated badge
 *  does not need. */
const ERROR_CORRECTION = "M" as const;

export interface EncodedQr {
  /** A `data:image/png` URL, for an `<img>` and for download. */
  dataUrl: string;
  /** The same code as SVG markup, for download at any size without pixellation. */
  svg: string;
  /** The symbol version the encoder chose, 1-40. */
  version: number;
  /** Modules per side, which is what actually governs scannability at a given size. */
  modules: number;
  /** True when the code is dense enough that a small print may not scan. */
  dense: boolean;
}

/** Message shown when a payload will not fit in one code.
 *
 *  Phrased as the action that fixes it. The library's own wording ("The amount of data is
 *  too big to be stored in a QR Code") is accurate and tells nobody what to do about it,
 *  and the thing to do is always the same: take a column off. */
export const TOO_LONG =
  "This payload is too long for one QR code. Remove a column — a long text column is " +
  "usually the cause.";

/** Encode a payload, or explain why it cannot be.
 *
 *  **`qrcode` is imported dynamically**, which is the same call `QrScanDialog` makes for
 *  `html5-qrcode`: QR is behind a menu action that most sessions never touch, and the
 *  encoder has no business in the entry chunk. `docs/capacity-and-performance.md` tracks
 *  the bundle to the kilobyte, so this must stay a dynamic import.
 *
 *  Both renderings come back from one call rather than two, because the expensive part is
 *  the encode and both the preview and the download want the result. */
export async function encode(text: string, size = 512): Promise<EncodedQr> {
  const QR = await import("qrcode");

  let version: number;
  let modules: number;
  try {
    // `create` is the only entry point that reports which version it chose, and it is
    // also the cheapest way to find out whether the payload fits at all — before two
    // renderings are spent on it.
    const symbol = QR.create(text, { errorCorrectionLevel: ERROR_CORRECTION });
    version = symbol.version;
    modules = symbol.modules.size;
  } catch {
    // The library throws a bare Error for an over-capacity payload. Anything else it
    // could throw here is also "this text cannot become a code", so one message covers
    // it rather than guessing at a taxonomy.
    throw new Error(TOO_LONG);
  }

  const options = {
    errorCorrectionLevel: ERROR_CORRECTION,
    // No quiet-zone padding beyond the spec's 4 modules: the sheet's own layout provides
    // the white space, and a wider margin here would shrink the code inside a fixed box.
    margin: 2,
    width: size,
  } as const;

  const [dataUrl, svg] = await Promise.all([
    QR.toDataURL(text, options),
    QR.toString(text, { ...options, type: "svg" }),
  ]);

  return {
    dataUrl,
    svg,
    version,
    modules,
    dense: version >= DENSE_VERSION_FROM,
  };
}
