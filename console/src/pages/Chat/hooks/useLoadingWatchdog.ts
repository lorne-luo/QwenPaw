import { useEffect } from "react";

/**
 * Rescue a composer whose loading state outlived its run.
 *
 * The runtime clears `loading` in exactly one place: `finishResponse`, reached
 * only when the stream delivers a terminal `{object:"response", status:...}`
 * event. Every other way a stream can end — a clean close with no terminal
 * event, a thrown error, an abort, a backend path that logs instead of emitting
 * (rate limits, unhandled errors), a stop issued from another tab — leaves
 * `loading` true forever. The composer then refuses to send, and the message
 * queue stops draining because every drain path is guarded on it.
 *
 * `useStreamRevive` already covers this, but only after a full 60s silence
 * window — long enough that a user gives up and hits stop first, which is the
 * only manual way out. This watchdog is the fast path to the same remedy: once
 * a loading stream has been quiet for a while, ask the backend whether the run
 * is actually still going, and if it is not, hand off to the same revive.
 *
 * Revive rather than a bare `setLoading(false)`: the runtime's own teardown
 * also stamps the message status and flushes the bubble, so just dropping the
 * flag would leave a bubble spinning forever. Remounting re-renders the turn
 * from persisted history, which is correct by construction.
 *
 * A still-running backend means the stream is merely slow, so nothing happens
 * and the check repeats a window later.
 */

/** Quiet time on a loading stream before the backend is worth asking. */
export const LOADING_SILENCE_MS = 8_000;

/** How often a loading chat is re-examined. */
export const LOADING_CHECK_INTERVAL_MS = 4_000;

/**
 * Whether the backend should be probed now. `baseline` covers a run that has
 * not produced a single chunk yet, so a stream that dies before its first token
 * is still caught.
 */
export const shouldProbeChatStatus = (
  lastEventAt: number,
  baseline: number,
  now: number,
  silenceMs: number = LOADING_SILENCE_MS,
): boolean => now - Math.max(lastEventAt, baseline) >= silenceMs;

export type LoadingWatchdogOptions = {
  /** True while the runtime still believes a response is streaming. */
  loading: boolean;
  /** Chat id to ask the backend about; falsy disables the watchdog. */
  chatId: string;
  /** Agent id for the status request, so an agent switch cannot misread. */
  agentId?: string;
  /** Timestamp of the last SSE chunk, maintained by the response parser. */
  lastEventAtRef: { current: number };
  /** Resolves to true when the backend reports the run is no longer running. */
  isChatIdle: (
    chatId: string,
    signal: AbortSignal,
    agentId?: string,
  ) => Promise<boolean | null>;
  /** Called once when loading is confirmed stale; remounts the chat. */
  onStuck: () => void;
};

export function useLoadingWatchdog({
  loading,
  chatId,
  agentId,
  lastEventAtRef,
  isChatIdle,
  onStuck,
}: LoadingWatchdogOptions): void {
  useEffect(() => {
    if (!loading || !chatId) return;

    // Silence baseline. Moved forward after every probe so a genuinely long
    // think phase (the backend only heartbeats every 25s) is re-checked once
    // per silence window rather than on every tick.
    let baseline = Date.now();
    const controller = new AbortController();
    let disposed = false;
    let probing = false;

    const check = async () => {
      // A hidden tab has no visible stream to rescue, and a phone coming back
      // is handled by useStreamRevive's own wake-up path.
      if (document.visibilityState !== "visible") return;
      if (probing) return;
      if (
        !shouldProbeChatStatus(lastEventAtRef.current, baseline, Date.now())
      ) {
        return;
      }
      probing = true;
      try {
        baseline = Date.now();
        const idle = await isChatIdle(chatId, controller.signal, agentId);
        if (disposed || idle !== true) return;
        // One rescue per loading cycle. The remount normally takes this hook
        // out of existence anyway, but if it does not we must neither probe nor
        // remount in a loop.
        disposed = true;
        onStuck();
      } finally {
        probing = false;
      }
    };

    const timer = setInterval(() => void check(), LOADING_CHECK_INTERVAL_MS);
    return () => {
      disposed = true;
      controller.abort();
      clearInterval(timer);
    };
  }, [loading, chatId, agentId, lastEventAtRef, isChatIdle, onStuck]);
}
