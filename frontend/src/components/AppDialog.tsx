import { Dialog, type DialogProps } from "primereact/dialog";

/** Below this, a dialog stops being a panel floating over the page and becomes
 *  nearly the whole screen — there is no room for it to be anything else. */
const BREAKPOINTS = { "960px": "80vw", "640px": "95vw" };

/**
 * A Dialog that fits on a phone.
 *
 * Every dialog in the app is sized in pixels (`style={{ width: 560 }}`), which is
 * wider than the screen it has to sit on. PrimeReact's `breakpoints` prop overrides
 * that width below a given viewport width — so this wrapper exists to apply the same
 * two breakpoints everywhere rather than have thirteen call sites each remember to.
 * The px width still governs on a desktop, which is what it was chosen for.
 *
 * Import this instead of primereact/dialog. It takes the same props.
 *
 * **Put the buttons in `footer`, not at the end of the children.** The content is
 * a scrollport (below), so an action row inside it scrolls away — which on the
 * column dialog meant Save sat under thirty fields and you had to go looking for
 * it. `footer` renders outside that scrollport and is styled as a real bar with
 * a rule above it; `themes.css` right-aligns it, so the footer is usually just
 * `<><Button text .../><Button .../></>`.
 */
export function AppDialog({ breakpoints, contentStyle, ...props }: DialogProps) {
  return (
    <Dialog
      breakpoints={breakpoints ?? BREAKPOINTS}
      // A tall dialog on a short screen must scroll its own body — otherwise the
      // buttons at the bottom are simply unreachable.
      contentStyle={{ maxHeight: "70vh", overflowY: "auto", ...contentStyle }}
      {...props}
    />
  );
}
