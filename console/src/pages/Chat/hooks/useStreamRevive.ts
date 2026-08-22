import { useEffect } from "react";

/**
 * Bring the chat back in sync after the tab was frozen or the stream died.
 *
 * A phone that turns its screen off freezes the tab and tears down the open
 * `POST /console/chat` SSE connection (a dropped wifi link does the same on
 * desktop). The runtime's read loop stops without a completion event, so the
 * response message stays `generating` and the composer stays disabled until
 * the user reloads the page.
 *
 * Reviving means remounting the chat: the session loader re-reads the chat
 * status and either re-attaches through the reconnect endpoint — the backend
 * replays every buffered event of the still-running run — or renders the
 * history persisted while the screen was off. Either way the user sees
 * everything that happened in the meantime.
 *
 * Three triggers, because the failure modes give different evidence:
 *
 *  - **A mobile screen came back.** A freeze long enough to drop the
 *    connection is revived on sight, without waiting for proof that anything is stale:
 *    waiting would delay the tail of the answer by up to a silence window,
 *    and a chat that was written to from another channel (IM, a scheduled
 *    task) while the screen was off shows no local symptom at all. The
 *    reload costs one session load and loses nothing. Desktop backgrounding
 *    is different: a healthy stream stays attached and is not remounted.
 *  - **The tab never left.** No wake-up to react to, so silence is the only
 *    evidence. The backend heartbeats every 25s while a run is alive, so a
 *    stream quiet for a full minute is dead, not slow. Only a live run can
 *    go silent, so this trigger applies while streaming.
 */

/** Longest silence tolerated on a live stream before it counts as dead. */
export const STREAM_SILENCE_MS = 60_000;

/** How often a streaming chat is checked for silence. */
export const STREAM_CHECK_INTERVAL_MS = 15_000;

/**
 * Hidden long enough that the connection is assumed gone on resume.
 * Short enough to cover a phone screen tapped off and straight back on,
 * long enough that flicking between desktop tabs does not remount the chat.
 */
export const HIDDEN_KILL_MS = 3_000;

export const isStreamDead = (
  lastEventAt: number,
  now: number,
  silenceMs: number = STREAM_SILENCE_MS,
): boolean => lastEventAt > 0 && now - lastEventAt >= silenceMs;

export type StreamReviveOptions = {
  /** True while the runtime still believes a response is streaming. */
  streaming: boolean;
  /** Mobile pages are expected to be frozen when their screen turns off. */
  mobile: boolean;
  /** Timestamp of the last SSE chunk; the hook stamps it when a run starts. */
  lastEventAtRef: { current: number };
  /** Remounts the chat so the session loader re-attaches to the run. */
  revive: () => void | Promise<void>;
};

export function useStreamRevive({
  streaming,
  mobile,
  lastEventAtRef,
  revive,
}: StreamReviveOptions): void {
  useEffect(() => {
    // A run that has produced no chunk yet still needs a silence baseline.
    if (streaming) lastEventAtRef.current = Date.now();

    let hiddenAt = document.visibilityState === "hidden" ? Date.now() : 0;

    const doRevive = () => {
      // Re-baseline first: if the reload cannot restore the stream (backend
      // down), the next attempt is a silence window away, not a check away.
      lastEventAtRef.current = Date.now();
      void revive();
    };

    const onVisibility = () => {
      if (document.visibilityState !== "visible") {
        hiddenAt = Date.now();
        return;
      }
      const hiddenMs = hiddenAt ? Date.now() - hiddenAt : 0;
      hiddenAt = 0;
      if (mobile && hiddenMs >= HIDDEN_KILL_MS) {
        doRevive();
        return;
      }
      if (streaming && isStreamDead(lastEventAtRef.current, Date.now())) {
        doRevive();
      }
    };

    // A bfcache restore always comes back with a dead connection. A plain
    // load does not (`persisted` is false there) — reviving on that would
    // reload the chat once more right after it first rendered.
    const onPageShow = (event: PageTransitionEvent) => {
      hiddenAt = 0;
      if (!event.persisted) return;
      if (document.visibilityState === "visible") doRevive();
    };

    // Nothing to react to while the tab stays put, so silence is the signal.
    // A hidden tab is never revived: it runs no timers anyway, and reloading
    // one would throw away the run we want to re-attach to.
    const check = () => {
      if (document.visibilityState !== "visible") return;
      if (!isStreamDead(lastEventAtRef.current, Date.now())) return;
      doRevive();
    };

    const timer = streaming
      ? setInterval(check, STREAM_CHECK_INTERVAL_MS)
      : null;
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pageshow", onPageShow);
    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pageshow", onPageShow);
    };
  }, [streaming, mobile, lastEventAtRef, revive]);
}
