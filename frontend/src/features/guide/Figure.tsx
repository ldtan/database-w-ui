import { useState } from "react";
import { Dialog } from "primereact/dialog";

import { useTheme } from "../../lib/theme";
import { FIGURES } from "./figures";

/**
 * A screenshot in the guide, with its caption, that opens full size when clicked.
 *
 * The pictures live in `public/guide/` and are taken by
 * `frontend/scripts/capture-guide.mjs` against a real running app — see that file
 * for how to add one. Nothing here fetches: they are static assets, so a figure
 * costs the guide a request and no state.
 *
 * **Two files per picture, one per mode.** A light screenshot in a dark page reads
 * as a hole punched in it, so every shot is taken twice and this picks by the
 * mode the reader is in. It is a plain `src` swap rather than a `<picture>` with
 * `prefers-color-scheme`, because this app's light/dark is an account preference
 * that need not agree with the operating system's.
 *
 * **An unknown name renders nothing.** A guide that names a figure nobody has
 * captured yet should be a guide missing a picture, not a blank page — and the
 * capture script is the thing that decides what exists.
 */
export function Figure({ name }: { name: string }) {
  const { mode } = useTheme();
  const [zoomed, setZoomed] = useState(false);
  const figure = FIGURES[name];
  if (!figure) return null;

  const src = `/guide/${name}-${mode === "dark" ? "dark" : "light"}.png`;

  return (
    <figure className="guide-figure m-0 flex flex-col gap-2">
      <button
        type="button"
        className="guide-figure-frame"
        onClick={() => setZoomed(true)}
        // The alt text is on the image; this button's job is to say what
        // clicking does, which the picture itself does not.
        aria-label={`Enlarge: ${figure.caption}`}
      >
        <img src={src} alt={figure.alt} loading="lazy" />
        <span className="guide-figure-zoom" aria-hidden>
          <i className="pi pi-search-plus" />
        </span>
      </button>
      <figcaption className="text-sm" style={{ color: "var(--text-muted)" }}>
        {figure.caption}
      </figcaption>

      {/* Full width of the window rather than a fixed size: these are screenshots
          of a 1400px-wide app, and a dialog narrower than that is a second
          thumbnail. Mounted only while open so twenty figures on one page are
          twenty images, not twenty images and twenty dialogs. */}
      <Dialog
        visible={zoomed}
        onHide={() => setZoomed(false)}
        header={figure.caption}
        dismissableMask
        style={{ width: "95vw", maxWidth: 1500 }}
        // Some of these are portrait — a tall dialog photographed whole — and
        // without a ceiling the enlarged copy runs off the bottom of the window
        // with its own close button below the fold.
        contentStyle={{ padding: 0, maxHeight: "80vh", overflow: "auto" }}
      >
        <img
          src={src}
          alt={figure.alt}
          style={{ width: "100%", display: "block" }}
        />
      </Dialog>
    </figure>
  );
}
