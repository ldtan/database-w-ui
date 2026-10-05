import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * What a deployment's env vars are allowed to mean.
 *
 * `config.ts` reads `import.meta.env` once, at import, so these tests stub the
 * variable and then re-import the module rather than calling a helper directly —
 * which keeps the helpers private and tests the thing a deploy actually sets. The
 * two cases worth pinning are the **defaults** (what a deployment that configures
 * nothing gets) and the **unrecognised value** (a flag that falls back rather than
 * reading a typo as "off", because an off flag is indistinguishable from a broken one).
 *
 * Every variable is stubbed explicitly, including to `undefined`. Vitest loads
 * `.env.local`, which is git-ignored and sets `VITE_LIVE_REFRESH_SECONDS` on at least
 * one machine — so "unset" has to be *made* unset here, or the defaults assert
 * whatever the person running the suite happens to have configured.
 */

const VARS = [
  "VITE_HOME_ENABLED",
  "VITE_LIVE_REFRESH_SECONDS",
  "VITE_METRIC_REFRESH_SECONDS",
  "VITE_COUNT_REFRESH_SECONDS",
  "VITE_ARCHIVE_HINT_THRESHOLD",
] as const;

/** Load a fresh copy of the module with `overrides` applied and nothing else set. */
async function loadConfig(overrides: Record<string, string | undefined> = {}) {
  for (const name of VARS) vi.stubEnv(name, overrides[name]);
  // The module caches its reads at evaluation time, so a stubbed env is only seen
  // by a fresh copy of it.
  vi.resetModules();
  return import("./config");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("HOME_ENABLED", () => {
  it("is off when the deployment says nothing", async () => {
    expect((await loadConfig()).HOME_ENABLED).toBe(false);
  });

  it("accepts the spellings a deployment dashboard produces", async () => {
    for (const raw of ["true", "True", "TRUE", " true ", "1", "yes", "on"]) {
      const config = await loadConfig({ VITE_HOME_ENABLED: raw });
      expect(config.HOME_ENABLED, raw).toBe(true);
    }
  });

  it("is off for every spelling of no", async () => {
    for (const raw of ["false", "False", "0", "no", "off"]) {
      const config = await loadConfig({ VITE_HOME_ENABLED: raw });
      expect(config.HOME_ENABLED, raw).toBe(false);
    }
  });

  it("falls back on a value it does not recognise", async () => {
    // Not "off" by coincidence: the default is off, and quietly reading `maybe` as
    // off would make a misconfigured flag look exactly like a working one.
    for (const raw of ["maybe", "", "enabled"]) {
      const config = await loadConfig({ VITE_HOME_ENABLED: raw });
      expect(config.HOME_ENABLED, raw).toBe(false);
    }
  });
});

describe("the numeric settings", () => {
  it("fall back to their documented defaults when unset", async () => {
    const config = await loadConfig();
    expect(config.LIVE_REFRESH_SECONDS).toBe(10);
    expect(config.METRIC_REFRESH_SECONDS).toBe(60);
    expect(config.COUNT_REFRESH_SECONDS).toBe(30);
    expect(config.ARCHIVE_HINT_THRESHOLD).toBe(25000);
  });

  it("read a number, and reject one that cannot be an interval", async () => {
    expect(
      (await loadConfig({ VITE_LIVE_REFRESH_SECONDS: "5" }))
        .LIVE_REFRESH_SECONDS,
    ).toBe(5);

    // 0 is meaningful — it disables the polling fallback — so it must survive.
    expect(
      (await loadConfig({ VITE_LIVE_REFRESH_SECONDS: "0" }))
        .LIVE_REFRESH_SECONDS,
    ).toBe(0);

    for (const raw of ["-1", "soon", ""]) {
      const config = await loadConfig({ VITE_LIVE_REFRESH_SECONDS: raw });
      expect(config.LIVE_REFRESH_SECONDS, raw).toBe(10);
    }
  });
});
