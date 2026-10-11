import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import { Button } from "primereact/button";

import { SMALL_SCREEN, useMediaQuery } from "../../lib/useMediaQuery";
import type { Tour, TourStep } from "./tours";

/**
 * The tour itself: everything dimmed except one thing, and a card beside it
 * saying what that thing is.
 *
 * Three layers, because one element cannot do all three jobs. A transparent
 * blocker swallows clicks so a tour cannot be half-taken while the page moves
 * underneath it; the spotlight is an empty box with a very large box-shadow,
 * which is what paints the dimming *around* a hole rather than over it; and the
 * card sits above both. The shadow is not part of the spotlight's hit area,
 * which is exactly why the blocker underneath it has to exist.
 */

/** How far the dimming is cut back from the highlighted element. */
const PAD = 6;
/** Between the spotlight and the card. */
const GAP = 12;
/** The card's width on anything but a phone, where it becomes a bottom sheet. */
const CARD_W = 340;
/** Keeps the card off the very edge of the window. */
const MARGIN = 12;

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

function same(a: Box | null, b: Box | null): boolean {
  if (!a || !b) return a === b;
  return (
    Math.abs(a.top - b.top) < 1 &&
    Math.abs(a.left - b.left) < 1 &&
    Math.abs(a.width - b.width) < 1 &&
    Math.abs(a.height - b.height) < 1
  );
}

/**
 * Where the step's element is on screen right now, or null while there is no
 * such element.
 *
 * A frame loop rather than listeners. The element can arrive late (the tour
 * navigates to a page whose data is still loading), move (the page it is on
 * fades in, a workspace's tables expand above it), or go away entirely — and
 * scroll, resize, layout and animation are four separate subscriptions that
 * still would not catch all of that. One `querySelector` per frame for the
 * length of a tour is cheap, and it is only mounted while a tour is running.
 *
 * The first sighting scrolls the element into view; after that the loop only
 * measures, so a person can still scroll the page and watch the spotlight track
 * what they are reading about.
 */
function useAnchorBox(anchor: string | undefined): Box | null {
  const [box, setBox] = useState<Box | null>(null);

  useEffect(() => {
    if (!anchor) {
      setBox(null);
      return;
    }
    let frame = 0;
    let scrolled = false;
    const selector = `[data-tour="${anchor}"]`;

    const measure = () => {
      frame = requestAnimationFrame(measure);
      const el = document.querySelector(selector);
      if (!el) {
        setBox((prev) => (prev === null ? prev : null));
        return;
      }
      if (!scrolled) {
        scrolled = true;
        // themes.css turns the app's animations off for anyone who has asked
        // their system for that; a scroll started from script is not something
        // CSS can reach, so it has to ask the same question itself.
        el.scrollIntoView({
          block: "center",
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "auto"
            : "smooth",
        });
      }
      const r = el.getBoundingClientRect();
      // A zero-sized element is one that is in the DOM but not laid out yet —
      // treat it as absent rather than spotlighting a point.
      const next =
        r.width < 1 || r.height < 1
          ? null
          : { top: r.top, left: r.left, width: r.width, height: r.height };
      setBox((prev) => (same(prev, next) ? prev : next));
    };

    frame = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(frame);
  }, [anchor]);

  return box;
}

/** The window, as something the render can depend on. The spotlight tracks its
 *  element frame by frame, but a window resized while the card sits in the
 *  middle of nothing would otherwise be placed against edges that have moved. */
function useViewport() {
  const [view, setView] = useState({
    width: window.innerWidth,
    height: window.innerHeight,
  });
  useEffect(() => {
    const onResize = () =>
      setView({ width: window.innerWidth, height: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return view;
}

/** The card's own size, so it can be placed against an edge without overhanging
 *  one. Measured rather than assumed: the steps are different lengths. */
function useCardSize(ref: RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState({ width: CARD_W, height: 200 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      setSize((prev) =>
        Math.abs(prev.width - r.width) < 1 && Math.abs(prev.height - r.height) < 1
          ? prev
          : { width: r.width, height: r.height },
      );
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

/**
 * Put the card beside the spotlight: under it by preference, over it when there
 * is no room below, and to one side when the element is tall enough that neither
 * fits. Whatever is chosen, it is then pushed back inside the window — a card
 * half off the screen is worse than a card in the wrong place.
 */
function placeCard(
  box: Box,
  card: { width: number; height: number },
  view: { width: number; height: number },
) {
  const below = box.top + box.height + PAD + GAP;
  const above = box.top - PAD - GAP - card.height;
  const roomBelow = below + card.height <= view.height - MARGIN;
  const roomAbove = above >= MARGIN;

  let top: number;
  let left = box.left + box.width / 2 - card.width / 2;

  if (roomBelow) {
    top = below;
  } else if (roomAbove) {
    top = above;
  } else {
    // Neither: sit beside it, on whichever side has more room, and centre
    // vertically on the element.
    top = box.top + box.height / 2 - card.height / 2;
    const rightEdge = box.left + box.width + PAD + GAP;
    left =
      view.width - rightEdge >= card.width + MARGIN
        ? rightEdge
        : box.left - PAD - GAP - card.width;
  }

  return {
    top: Math.min(Math.max(top, MARGIN), view.height - card.height - MARGIN),
    left: Math.min(Math.max(left, MARGIN), view.width - card.width - MARGIN),
  };
}

interface Props {
  tour: Tour;
  /** Already filtered to the steps that apply at this width. */
  steps: TourStep[];
  index: number;
  onIndex: (index: number) => void;
  /** `completed` is false when it was skipped or dismissed. */
  onClose: (completed: boolean) => void;
}

export function TourOverlay({ tour, steps, index, onIndex, onClose }: Props) {
  const step = steps[index];
  const box = useAnchorBox(step?.anchor);
  const cardRef = useRef<HTMLDivElement>(null);
  const card = useCardSize(cardRef);
  const view = useViewport();
  const phone = useMediaQuery(SMALL_SCREEN);

  const first = index === 0;
  const last = index === steps.length - 1;

  // Arrow keys to walk it, Escape to leave — a tour you cannot get out of with
  // the key everybody tries is a trap. Tab is kept inside the card: the page
  // behind cannot be clicked, so it must not be reachable by keyboard either,
  // which is the promise `aria-modal` on the card is making.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose(false);
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        if (last) onClose(true);
        else onIndex(index + 1);
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        if (!first) onIndex(index - 1);
        return;
      }
      if (e.key !== "Tab") return;
      const focusable = Array.from(
        cardRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
        ) ?? [],
      );
      if (!focusable.length) return;
      // Wrapping at the end, and also from the card itself — which holds focus at
      // the start of every step and is not in this table, so shift-tabbing off it
      // would otherwise land on the page behind.
      const from = document.activeElement as HTMLElement | null;
      const leaving = !from || !focusable.includes(from);
      const edge = e.shiftKey ? focusable[0] : focusable[focusable.length - 1];
      if (leaving || from === edge) {
        e.preventDefault();
        (e.shiftKey ? focusable[focusable.length - 1] : focusable[0]).focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, first, last, onIndex, onClose]);

  // The card is what the tour *is*, so it takes focus on every step: a screen
  // reader then reads the new step rather than leaving the person on whatever
  // they were focused on before the tour started.
  useEffect(() => {
    cardRef.current?.focus();
  }, [index]);

  if (!step) return null;

  // On a phone the card is a sheet across the bottom whatever is highlighted —
  // there is no room to place a 340px card beside anything, and a sheet keeps
  // the buttons where a thumb already is.
  const placed = box && !phone ? placeCard(box, card, view) : null;
  // No element to point at: the step is about an idea, or the thing it named is
  // not on this page. Either way the card goes to the middle of the window,
  // which `.is-centred` does — a transform, so it cannot also be the animation.
  const centred = !phone && !placed;
  const cardStyle: CSSProperties = phone
    ? { left: MARGIN, right: MARGIN, bottom: MARGIN, width: "auto" }
    : placed
      ? { top: placed.top, left: placed.left, width: CARD_W }
      : { width: CARD_W };

  return (
    <div role="dialog" aria-modal="true" aria-label={`${tour.label}: step ${index + 1} of ${steps.length}`}>
      <div className="tour-blocker" />

      {box ? (
        <div
          className="tour-spotlight"
          style={{
            top: box.top - PAD,
            left: box.left - PAD,
            width: box.width + PAD * 2,
            height: box.height + PAD * 2,
          }}
        />
      ) : (
        <div className="tour-scrim" />
      )}

      <div
        className={`tour-card${centred ? " is-centred" : ""}`}
        style={cardStyle}
        ref={cardRef}
        tabIndex={-1}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="badge is-brand">
            <i className={tour.icon} />
            {index + 1} of {steps.length}
          </span>
          <Button
            icon="pi pi-times"
            text
            rounded
            aria-label="Close the tour"
            onClick={() => onClose(false)}
          />
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold">{step.title}</h2>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            {step.body}
          </p>
          {step.points && (
            <ul className="tour-points">
              {step.points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="tour-footer">
          {/* Dots rather than a bar: eight of them says "this is short", which a
              progress bar at 12% does not. */}
          <div className="tour-dots" aria-hidden>
            {steps.map((s, i) => (
              <span
                key={s.title}
                className={`tour-dot${i === index ? " is-current" : ""}${
                  i < index ? " is-done" : ""
                }`}
              />
            ))}
          </div>
          <div className="flex items-center gap-1">
            <Button label="Back" text disabled={first} onClick={() => onIndex(index - 1)} />
            {last ? (
              <Button label="Done" icon="pi pi-check" onClick={() => onClose(true)} />
            ) : (
              <Button
                label="Next"
                icon="pi pi-arrow-right"
                iconPos="right"
                onClick={() => onIndex(index + 1)}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
