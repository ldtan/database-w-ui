// The client half of "live records": one event stream per table, shared by everyone
// on the page who cares about that table, telling them when to look again.
//
// Consumed by `useLiveTable`, which is the only thing that should import
// `subscribeToTable` — the components spread that hook's result into a `useQuery` and
// never see this file. `parseFrames` is exported separately because it is the one
// piece here that is pure, and therefore the one piece the test runner can reach
// (vitest runs in a node environment with no DOM — see vite.config.ts).

import { api } from "./api";

/** What a subscriber is told.
 *
 * Three states rather than two, because "nobody is looking" has to be distinct from
 * "push is unavailable": the first means refresh nothing at all, the second means
 * fall back to a timer. Collapsing them would leave an abandoned tab polling, which
 * is the exact cost the idle guard was written to remove.
 */
export type LiveStatus =
  | "live" // the stream is open; do not poll
  | "degraded" // it will not stay open; fall back to polling
  | "paused"; // tab hidden or user idle; do neither

export interface LiveSubscriber {
  /** This table changed — invalidate and refetch. */
  onChange: () => void;
  /** The transport's state changed. Decides push vs. poll at the call site. */
  onStatus: (status: LiveStatus) => void;
}

// Stop the stream after this long with no user activity, even while the tab is
// visible — and close it outright while the tab is hidden. Both mirror what the
// polling implementation did (`refetchIntervalInBackground: false` plus an idle
// timer), and keeping them is a deliberate choice rather than an oversight: a held
// socket is cheaper than a poll but it is not free, and the property worth
// preserving is that an abandoned tab costs *nothing*. Reopening fires one change,
// because whatever happened during the gap was missed.
const IDLE_AFTER_MS = 5 * 60 * 1000;

// Interactions that count as "still here". `visibilitychange` is included so
// returning to the tab reopens immediately rather than waiting out the timer.
const ACTIVITY_EVENTS = [
  "mousemove",
  "keydown",
  "pointerdown",
  "scroll",
  "touchstart",
  "visibilitychange",
] as const;

// Events are coalesced over this window before anyone is told. A bulk import or a
// multi-row delete is one refetch rather than one per row — the server already
// collapses a burst into a single frame, and this covers the case where it cannot
// (several separate writes landing together). It also absorbs the echo of your own
// mutation, which has already invalidated locally.
const COALESCE_MS = 300;

// Reconnect backoff. The server closes every stream at its own deadline, so the
// common "disconnect" is entirely routine and must reconnect immediately; the delays
// below are for a stream that failed. After DEGRADE_AFTER consecutive failures the
// table is declared degraded and polling takes over — the stream keeps trying in the
// background, so a proxy or network that starts cooperating is picked up without a
// reload.
const RETRY_MS = [1000, 2000, 5000, 15000, 30000];
const DEGRADE_AFTER = 2;

/** Split an SSE buffer into complete frames, returning whatever is left over.
 *
 * Frames are separated by a blank line, and a chunk from the network can end
 * anywhere — mid-frame, mid-line, even mid-UTF-8-character (which is why the caller
 * decodes with `{ stream: true }` and hands the text here rather than decoding per
 * chunk). The remainder must be carried into the next call or a frame split across
 * two reads is silently lost, which on this channel means a change nobody hears
 * about.
 *
 * Both line endings are handled: the spec allows CRLF, and a proxy may rewrite them.
 */
export function parseFrames(buffer: string): { frames: string[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const parts = normalized.split("\n\n");
  // The last piece is either an incomplete frame or "" when the buffer ended on a
  // separator. Either way it is not yet a frame.
  const rest = parts.pop() ?? "";
  return { frames: parts.filter((frame) => frame.trim() !== ""), rest };
}

/** The event name a frame carries, or null for a comment or a nameless frame. */
export function frameEvent(frame: string): string | null {
  for (const line of frame.split("\n")) {
    // A line starting with ':' is a comment — the heartbeat is one, and it must not
    // be mistaken for an event or an idle stream would refetch forever.
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) return line.slice("event:".length).trim();
  }
  return null;
}

interface Channel {
  subscribers: Set<LiveSubscriber>;
  abort: AbortController | null;
  /** Cancels a scheduled reconnect. */
  retry: ReturnType<typeof setTimeout> | null;
  coalesce: ReturnType<typeof setTimeout> | null;
  failures: number;
  /** Whether push is working, independent of whether anyone is looking. */
  transport: "live" | "degraded";
  /** True once the channel has been told to stop (idle, hidden, or no subscribers). */
  paused: boolean;
  /** What subscribers were last told, so they are only told about changes. */
  reported: LiveStatus | null;
  /** A change arrived while paused, or the stream was reopened after a gap. */
  missed: boolean;
}

// One channel per table uuid, for the whole tab. Three components ask for the same
// table — the grid, the paginator's total and the metric strip — and without this each
// would open its own connection to say the same thing three times.
const channels = new Map<string, Channel>();

// Whether the page is currently worth streaming for. Shared by every channel, since
// the tab is hidden or the user is away for all of them at once.
let active = true;
let idleTimer: ReturnType<typeof setTimeout> | null = null;
let activityBound = false;

function setActive(next: boolean): void {
  if (active === next) return;
  active = next;
  for (const [tableUuid, channel] of channels) {
    if (active) openChannel(tableUuid, channel);
    else pauseChannel(channel);
  }
}

function markActive(): void {
  if (idleTimer) clearTimeout(idleTimer);
  idleTimer = setTimeout(() => setActive(false), IDLE_AFTER_MS);
  setActive(!document.hidden);
}

function bindActivity(): void {
  if (activityBound) return;
  activityBound = true;
  for (const evt of ACTIVITY_EVENTS)
    window.addEventListener(evt, markActive, { passive: true });
  markActive();
}

/** Derived, never assigned: pausing must not lose whether push was working, or a
 * tab coming back from idle would poll once for no reason (or, worse, trust a
 * stream that had already failed). */
function statusOf(channel: Channel): LiveStatus {
  return channel.paused ? "paused" : channel.transport;
}

function publishStatus(channel: Channel): void {
  const status = statusOf(channel);
  if (channel.reported === status) return;
  channel.reported = status;
  for (const subscriber of channel.subscribers) subscriber.onStatus(status);
}

function emitChange(channel: Channel): void {
  if (channel.coalesce) return; // one refetch for a burst, not one per event
  channel.coalesce = setTimeout(() => {
    channel.coalesce = null;
    for (const subscriber of channel.subscribers) subscriber.onChange();
  }, COALESCE_MS);
}

function pauseChannel(channel: Channel): void {
  channel.paused = true;
  publishStatus(channel);
  if (channel.retry) {
    clearTimeout(channel.retry);
    channel.retry = null;
  }
  channel.abort?.abort();
  channel.abort = null;
  // Nothing is being listened for now, so anything that happens while paused is
  // missed by definition — say so on the way back in rather than pretend otherwise.
  channel.missed = true;
}

function scheduleRetry(tableUuid: string, channel: Channel): void {
  if (channel.paused || channel.subscribers.size === 0) return;
  const delay = RETRY_MS[Math.min(channel.failures, RETRY_MS.length - 1)];
  // Jitter, so a deploy that drops every stream at once does not bring them all
  // back in the same millisecond.
  const wait = delay * (0.5 + Math.random());
  channel.retry = setTimeout(() => {
    channel.retry = null;
    openChannel(tableUuid, channel);
  }, wait);
}

function openChannel(tableUuid: string, channel: Channel): void {
  channel.paused = false;
  // Resuming reports whatever the transport was doing before — `degraded` if it had
  // been failing, so the caller starts polling again immediately rather than sitting
  // silent until this attempt fails too.
  publishStatus(channel);
  if (channel.abort || channel.retry || channel.subscribers.size === 0) return;

  const controller = new AbortController();
  channel.abort = controller;

  void (async () => {
    try {
      const res = await api.stream(`/tables/${tableUuid}/events`, controller.signal);
      if (!res.ok || !res.body) {
        // 503 is the deployment's kill switch and 403/404 mean this table is not
        // ours to watch — none of them will improve by retrying sooner, and all of
        // them are correctly served by polling instead.
        throw new Error(`event stream refused: ${res.status}`);
      }

      channel.failures = 0;
      channel.transport = "live";
      publishStatus(channel);
      // A gap has just ended — while paused, while reconnecting, or on the very
      // first connect after a fallback poll. Ask for a refetch so the grid is
      // current from the moment the stream is trusted again.
      if (channel.missed) {
        channel.missed = false;
        emitChange(channel);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break; // the server closed at its deadline: reconnect below
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = parseFrames(buffer);
        buffer = rest;
        for (const frame of frames) {
          if (frameEvent(frame) === "records") emitChange(channel);
        }
      }
      // A clean end is the expected case (SSE_MAX_STREAM_SECONDS), so reconnect at
      // once rather than backing off — but note the gap, since a write could have
      // landed between the close and the next open.
      channel.abort = null;
      channel.missed = true;
      if (!channel.paused) openChannel(tableUuid, channel);
    } catch (err) {
      channel.abort = null;
      // An abort is us, not a failure: pausing and unsubscribing both go through it.
      if (controller.signal.aborted) return;
      channel.failures += 1;
      if (channel.failures >= DEGRADE_AFTER) {
        channel.transport = "degraded";
        publishStatus(channel);
      }
      scheduleRetry(tableUuid, channel);
    }
  })();
}

/**
 * Watch one table's records. Returns an unsubscribe function.
 *
 * The subscriber is told two things: `onChange` when the table's records have moved
 * (debounced, and carrying nothing — re-read through the ordinary permissioned
 * endpoint), and `onStatus` when push stops or starts being available, which is what
 * lets the caller fall back to polling instead of silently going stale.
 */
export function subscribeToTable(
  tableUuid: string,
  subscriber: LiveSubscriber,
): () => void {
  bindActivity();

  let channel = channels.get(tableUuid);
  if (!channel) {
    channel = {
      subscribers: new Set(),
      abort: null,
      retry: null,
      coalesce: null,
      failures: 0,
      // Starts degraded so a caller polls until the stream is actually open. The
      // other way round — assuming push works and correcting later — leaves a table
      // with no refresh at all for as long as it takes to find out otherwise.
      transport: "degraded",
      // A table opened while the tab is hidden, or by someone who has already been
      // idle five minutes, starts paused. Starting unpaused would report `degraded`
      // — the initial transport — and set the caller polling on a tab nobody is
      // looking at, which is the one thing the idle guard exists to prevent.
      paused: !active,
      reported: null,
      // Born paused means born behind: nothing is listening between now and the
      // moment the stream opens, so the first open owes a catch-up refetch. (A tab
      // opened in the background and looked at ten minutes later is the case.)
      missed: !active,
    };
    channels.set(tableUuid, channel);
  }
  channel.subscribers.add(subscriber);
  // Told directly rather than through `publishStatus`, which only announces
  // *changes* and would say nothing to a subscriber joining a channel already open.
  subscriber.onStatus(statusOf(channel));
  if (active) openChannel(tableUuid, channel);

  return () => {
    channel.subscribers.delete(subscriber);
    if (channel.subscribers.size > 0) return;
    // The last watcher of this table has gone: close the connection and forget the
    // table, rather than hold a socket for a page nobody is on.
    if (channel.coalesce) clearTimeout(channel.coalesce);
    pauseChannel(channel);
    channels.delete(tableUuid);
  };
}
