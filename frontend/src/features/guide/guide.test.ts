import { describe, expect, it } from "vitest";

import { FIGURES } from "./figures";
import { SECTIONS } from "./sections";
// Vite's own raw import rather than node's `fs`: it needs no `@types/node` (this
// project does not install them) and no path juggling to find the file from the
// test's own location. Typed by `vite/client`, already referenced in vite-env.d.ts.
import SOURCE from "./GuidePage.tsx?raw";

/**
 * What holds the guide together, checked.
 *
 * `docs/known-gaps.md` is right that most of this page cannot be tested: nothing
 * can tell prose that the button it names has been renamed. But four things here
 * are not prose, they are references, and a reference either resolves or it
 * doesn't. Those are worth pinning, and one of them fails *silently* today —
 * `Figure` returns null for a name nobody captured, so a typo is a screenshot
 * that is simply absent, with no error anywhere to say so.
 *
 * Vitest runs in a node environment with no jsdom (see `vite.config.ts`), so this
 * cannot mount the page. It doesn't need to: the contents list and the figure
 * captions are plain data modules, and the references into them are literals in
 * the page's source. Reading that source as text is enough, and it keeps the test
 * free of React, the router and PrimeReact, none of which it has any opinion about.
 */

/** The id of every `<Section>` the page renders, in source order.
 *
 *  `[\s\S]*?` rather than `\s*` between the tag and the attribute because several
 *  sections are written across multiple lines with other props first — a pattern
 *  anchored to the same line misses `change-type` and `nested-lookups` and reports
 *  two sections as missing when both are right there. Bounded by `>` so it cannot
 *  run past the end of the opening tag into the next one. */
function renderedSectionIds(): string[] {
  return [...SOURCE.matchAll(/<Section[^>]*?\sid="([^"]+)"/g)].map((m) => m[1]);
}

/** Every figure the page asks for. */
function referencedFigureNames(): string[] {
  return [...SOURCE.matchAll(/<Figure\s+name="([^"]+)"/g)].map((m) => m[1]);
}

/** Every in-page link the prose makes, e.g. `href="#sharing"`. */
function inPageAnchorTargets(): string[] {
  return [...SOURCE.matchAll(/href="#([^"]+)"/g)].map((m) => m[1]);
}

describe("the guide's contents list", () => {
  it("has a section on the page for every entry, and no others", () => {
    const listed = SECTIONS.map((s) => s.id);
    // Compared as sorted sets so the failure names the id that is wrong rather
    // than every entry after the first one that moved.
    expect([...renderedSectionIds()].sort()).toEqual([...listed].sort());
  });

  it("lists sections in the order the page renders them", () => {
    // The contents list is a reading order, not a set: an entry in the wrong
    // place sends people backwards through the page.
    expect(renderedSectionIds()).toEqual(SECTIONS.map((s) => s.id));
  });

  it("has no duplicate ids", () => {
    const ids = SECTIONS.map((s) => s.id);
    expect(ids).toEqual([...new Set(ids)]);
  });
});

describe("the guide's figures", () => {
  it("only names screenshots that have been captured", () => {
    // The one that fails silently in the app: `Figure` renders nothing at all for
    // an unknown name, so without this a typo is an invisible missing picture.
    const uncaptured = referencedFigureNames().filter((n) => !(n in FIGURES));
    expect(uncaptured).toEqual([]);
  });

  it("uses every screenshot that has been captured", () => {
    // The other direction: a shot the capture script still takes but no section
    // shows any more is dead weight in the repo and a hint someone cut a section.
    const referenced = new Set(referencedFigureNames());
    const unused = Object.keys(FIGURES).filter((n) => !referenced.has(n));
    expect(unused).toEqual([]);
  });
});

describe("the guide's in-page links", () => {
  it("point at sections that exist", () => {
    const ids = new Set(SECTIONS.map((s) => s.id));
    // `guide-contents` is the contents nav itself — the target of every section's
    // "↑ Contents" link, and deliberately not a section.
    const dead = [...new Set(inPageAnchorTargets())].filter(
      (target) => target !== "guide-contents" && !ids.has(target),
    );
    expect(dead).toEqual([]);
  });

  it("still has the contents nav the back-links point at", () => {
    expect(SOURCE).toContain('id="guide-contents"');
  });

  it("keeps the tour's hook on the contents nav", () => {
    // A step of the `basics` tour spotlights this nav by its data-tour attribute.
    // Losing it doesn't break the build or the tour — the step just degrades to a
    // card floating in the middle of the window, which nothing else would catch.
    expect(SOURCE).toContain('data-tour="guide-contents"');
  });
});
