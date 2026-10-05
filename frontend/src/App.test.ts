import { describe, expect, it } from "vitest";

// Vite's raw import, as `guide.test.ts` does it: no `@types/node` and no path
// juggling, and this test has no opinion about React or the router — only about the
// order two blocks appear in the file.
import SOURCE from "./App.tsx?raw";

/**
 * The one thing in the route table that is load-bearing and invisible.
 *
 * With `HOME_ENABLED` on, `/` and `/guide` each match twice — once in the public
 * layout and once in the guarded one — with the same specificity. React Router breaks
 * that tie by **declaration order**, so the public block has to come first. Reverse
 * them and a signed-out visitor is redirected to the sign-in form: the home page
 * stops existing, while every other route behaves perfectly.
 *
 * It is written down in a comment there, which is not enough on its own. Everybody
 * who works on this app is signed in, and a signed-in user is served the identical
 * app either way — so this is a regression nobody would meet by using the thing they
 * just changed. That asymmetry is what earns it a test; the same reasoning `Figure`'s
 * silent-null gets in `guide.test.ts`.
 *
 * Matching source text is deliberately crude, and the assertions are chosen to fail
 * on the mistake rather than on a reformat: an index comparison survives rewrapping,
 * renaming props and moving routes within either block.
 */

const publicLayoutAt = SOURCE.indexOf("<PublicLayout />");
const guardedLayoutAt = SOURCE.indexOf("<RequireAuth>");

describe("the route table", () => {
  it("still has both layouts in it", () => {
    // If either of these moved or was renamed, the ordering assertion below would
    // pass vacuously on -1. Fail here instead, where the message says what happened.
    expect(publicLayoutAt, "PublicLayout is no longer routed in App.tsx").
      toBeGreaterThan(-1);
    expect(guardedLayoutAt, "RequireAuth is no longer routed in App.tsx").
      toBeGreaterThan(-1);
  });

  it("declares the public layout before the guarded one", () => {
    expect(publicLayoutAt).toBeLessThan(guardedLayoutAt);
  });

  it("gates the public routes on both the flag and the absence of a session", () => {
    // `!isAuthenticated` is what lets the dashboard stay at `/`: drop it and a
    // signed-in user gets the landing page instead of their dashboard, which is the
    // other half of the same decision.
    expect(SOURCE).toContain("HOME_ENABLED && !isAuthenticated");
  });

  it("keeps the home page out of the main bundle", () => {
    // A deployment with the flag off should never fetch it. A static import would
    // still work, so nothing else would notice this being undone.
    expect(SOURCE).toMatch(/lazy\(\s*\(\)\s*=>\s*\n?\s*import\("\.\/features\/home\/HomePage"\)/);
  });
});
