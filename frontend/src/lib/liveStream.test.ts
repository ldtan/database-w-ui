import { describe, expect, it } from "vitest";

import { frameEvent, parseFrames } from "./liveStream";

// What is testable here and what is not, stated once. vitest runs in a **node**
// environment with no jsdom (vite.config.ts), deliberately — what it pins is rules a
// value goes through, not components that mount. So `subscribeToList` is out of reach:
// it reads `document`, `window` and `fetch`, and holds a connection. These two
// functions are the parts with no environment in them, and they are also where the
// quiet bugs live — a frame split across two network reads is lost silently, and a
// heartbeat mistaken for an event turns an idle stream into a refetch loop.

describe("parseFrames", () => {
  it("splits complete frames on the blank line between them", () => {
    const { frames, rest } = parseFrames("event: records\ndata: {}\n\n: ping\n\n");
    expect(frames).toEqual(["event: records\ndata: {}", ": ping"]);
    expect(rest).toBe("");
  });

  // The bug this pins: a chunk from the network ends wherever TCP says it does,
  // which is routinely mid-frame. A parser that dropped the remainder would lose
  // exactly the frames that arrived under load, and lose them silently — the client
  // would simply never hear about that change.
  it("holds an incomplete frame back for the next chunk", () => {
    const first = parseFrames("event: reco");
    expect(first.frames).toEqual([]);
    expect(first.rest).toBe("event: reco");

    const second = parseFrames(first.rest + "rds\ndata: {}\n\n");
    expect(second.frames).toEqual(["event: records\ndata: {}"]);
    expect(second.rest).toBe("");
  });

  it("handles a separator split across two chunks", () => {
    const first = parseFrames("event: records\ndata: {}\n");
    expect(first.frames).toEqual([]);
    const second = parseFrames(first.rest + "\n");
    expect(second.frames).toEqual(["event: records\ndata: {}"]);
  });

  // The spec permits CRLF and a proxy may rewrite line endings on the way through,
  // so neither the parser nor anything downstream may assume "\n".
  it("reads CRLF the same as LF", () => {
    const { frames } = parseFrames("event: records\r\ndata: {}\r\n\r\n");
    expect(frames).toEqual(["event: records\ndata: {}"]);
  });

  it("ignores empty stretches rather than emitting blank frames", () => {
    const { frames } = parseFrames("\n\n\n\nevent: records\n\n");
    expect(frames).toEqual(["event: records"]);
  });
});

describe("frameEvent", () => {
  it("names an event frame", () => {
    expect(frameEvent("event: records\ndata: {}")).toBe("records");
  });

  // The bug this pins: the heartbeat is a comment, and an idle stream is mostly
  // heartbeats. Reading one as an event would make a list nobody is touching refetch
  // every SSE_HEARTBEAT_SECONDS — worse than the polling this replaced, and only on
  // quiet lists, which is where nobody would look for it.
  it("is silent about a comment", () => {
    expect(frameEvent(": ping")).toBeNull();
  });

  it("is silent about a frame with no event name", () => {
    expect(frameEvent("data: {}")).toBeNull();
  });

  it("does not read an event name out of a comment", () => {
    expect(frameEvent(": event: records")).toBeNull();
  });
});
