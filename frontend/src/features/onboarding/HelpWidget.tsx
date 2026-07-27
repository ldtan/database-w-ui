import { useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { Button } from "primereact/button";
import { OverlayPanel } from "primereact/overlaypanel";

import { useOnboarding } from "./OnboardingProvider";
import { TOURS, pageTourFor, tipsFor, type Tour } from "./tours";

/**
 * The button in the corner, and everything behind it.
 *
 * It exists because the guide is a page you have to decide to go to, and the
 * moment somebody needs it is the moment they are in the middle of something
 * else. So: one target that never moves, holding the tour, the two or three
 * things worth knowing about the page under it, and the way to the long version.
 *
 * A dot appears on it while there is a tour here that has not been taken — a
 * quieter way of saying "there is something for you on this page" than opening
 * anything uninvited.
 */

/** A tour, offered as a row you can press. */
function TourRow({
  tour,
  seen,
  onStart,
}: {
  tour: Tour;
  seen: boolean;
  onStart: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onStart}
      className="surface-card is-interactive p-3 flex items-center gap-3 text-left w-full"
      style={{ background: "var(--surface)", color: "var(--text)", cursor: "pointer" }}
    >
      <span className="entity-icon" aria-hidden>
        <i className={tour.icon} />
      </span>
      <span className="flex flex-col min-w-0 flex-1">
        <span className="font-medium">{tour.label}</span>
        <span className="text-sm" style={{ color: "var(--text-muted)" }}>
          {tour.blurb}
        </span>
      </span>
      {seen ? (
        <i className="pi pi-check" style={{ color: "var(--brand)" }} aria-label="Already taken" />
      ) : (
        <i className="pi pi-arrow-right" style={{ color: "var(--text-muted)" }} aria-hidden />
      )}
    </button>
  );
}

export function HelpWidget() {
  const panel = useRef<OverlayPanel>(null);
  const { pathname } = useLocation();
  const { startTour, hasSeen, runningTour } = useOnboarding();

  const pageTour = pageTourFor(pathname);
  const tips = tipsFor(pathname);
  // The page's own tour is the more useful of the two when there is one, so it
  // goes first; "Show me around" is always offered under it.
  const tours = pageTour ? [pageTour, TOURS.basics] : [TOURS.basics];
  const unseen = tours.some((t) => !hasSeen(t.id));

  function start(id: Tour["id"]) {
    panel.current?.hide();
    startTour(id);
  }

  return (
    <>
      {/* The dot is a sibling rather than a child of the button: PrimeReact wraps
          a button's children in its own label span, which is not a thing to hang
          an absolutely-positioned dot off. */}
      <div className="help-fab">
        <Button
          data-tour="help-button"
          icon="pi pi-question"
          rounded
          aria-label="Help, tips and tours"
          aria-haspopup
          onClick={(e) => panel.current?.toggle(e)}
        />
        {/* Hidden once a tour is running: the last step of the basics tour points
            at this button, and a "there is something here" dot on the thing being
            explained is noise at exactly the wrong moment. */}
        {unseen && !runningTour && <span className="help-fab-dot" aria-hidden />}
      </div>

      <OverlayPanel ref={panel} className="help-panel">
        <div className="flex flex-col gap-3" style={{ width: "min(20rem, 80vw)" }}>
          <div className="flex flex-col gap-0.5">
            <span className="font-semibold">Need a hand?</span>
            <span className="text-sm" style={{ color: "var(--text-muted)" }}>
              Take a tour, or read the two-line version for this page.
            </span>
          </div>

          <div className="flex flex-col gap-2">
            {tours.map((t) => (
              <TourRow
                key={t.id}
                tour={t}
                seen={hasSeen(t.id)}
                onStart={() => start(t.id)}
              />
            ))}
          </div>

          <div className="flex flex-col gap-2">
            <span className="nav-section">On this page</span>
            <ul className="help-tips">
              {tips.map((tip) => (
                <li key={tip.text}>
                  <i className={tip.icon} aria-hidden />
                  <span>{tip.text}</span>
                </li>
              ))}
            </ul>
          </div>

          <Link
            to="/guide"
            className="no-underline text-sm font-medium"
            style={{ color: "var(--brand)" }}
            onClick={() => panel.current?.hide()}
          >
            Read the full guide →
          </Link>
        </div>
      </OverlayPanel>
    </>
  );
}
