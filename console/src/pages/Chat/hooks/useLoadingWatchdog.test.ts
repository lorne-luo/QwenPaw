import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  shouldProbeChatStatus,
  useLoadingWatchdog,
  LOADING_SILENCE_MS,
  LOADING_CHECK_INTERVAL_MS,
} from "./useLoadingWatchdog";

const START = 1_000_000;
const CHAT_ID = "chat-1";

const setVisibility = (state: DocumentVisibilityState) => {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
};

/** Advance the fake clock past the silence window and let the timer fire. */
const advancePastSilence = async () => {
  await vi.advanceTimersByTimeAsync(
    LOADING_SILENCE_MS + LOADING_CHECK_INTERVAL_MS,
  );
};

describe("shouldProbeChatStatus", () => {
  it("waits out the silence window", () => {
    expect(
      shouldProbeChatStatus(1_000, 0, 1_000 + LOADING_SILENCE_MS - 1),
    ).toBe(false);
  });

  it("probes once the stream has been quiet long enough", () => {
    expect(shouldProbeChatStatus(1_000, 0, 1_000 + LOADING_SILENCE_MS)).toBe(
      true,
    );
  });

  it("uses the baseline for a run with no chunks yet", () => {
    // lastEventAt is stale (or zero) but the run only just started, so the
    // stream has not actually been silent for the window.
    expect(shouldProbeChatStatus(0, 5_000, 5_000 + 1)).toBe(false);
    expect(shouldProbeChatStatus(0, 5_000, 5_000 + LOADING_SILENCE_MS)).toBe(
      true,
    );
  });
});

describe("useLoadingWatchdog", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(START);
    setVisibility("visible");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const mount = (
    opts: {
      loading?: boolean;
      chatId?: string;
      idle?: boolean | null;
    } = {},
  ) => {
    const { loading = true, chatId = CHAT_ID, idle = true } = opts;
    const onStuck = vi.fn();
    const isChatIdle = vi.fn().mockResolvedValue(idle);
    const lastEventAtRef = { current: 0 };
    const view = renderHook(
      (props: { loading: boolean }) =>
        useLoadingWatchdog({
          loading: props.loading,
          chatId,
          agentId: "agent-1",
          lastEventAtRef,
          isChatIdle,
          onStuck,
        }),
      { initialProps: { loading } },
    );
    return { onStuck, isChatIdle, lastEventAtRef, view };
  };

  it("revives the chat when a quiet stream turns out to be finished", async () => {
    const { onStuck, isChatIdle } = mount({ idle: true });

    await advancePastSilence();

    expect(isChatIdle).toHaveBeenCalledWith(
      CHAT_ID,
      expect.anything(),
      "agent-1",
    );
    expect(onStuck).toHaveBeenCalledTimes(1);
  });

  it("leaves a slow but still-running stream alone", async () => {
    const { onStuck, isChatIdle } = mount({ idle: false });

    await advancePastSilence();

    expect(isChatIdle).toHaveBeenCalled();
    expect(onStuck).not.toHaveBeenCalled();
  });

  it("does nothing when the probe was aborted", async () => {
    const { onStuck } = mount({ idle: null });

    await advancePastSilence();

    expect(onStuck).not.toHaveBeenCalled();
  });

  it("does not probe while the stream is still producing chunks", async () => {
    const { isChatIdle, lastEventAtRef } = mount();

    // Keep stamping activity just inside the silence window.
    for (let i = 0; i < 4; i++) {
      lastEventAtRef.current = Date.now();
      await vi.advanceTimersByTimeAsync(LOADING_CHECK_INTERVAL_MS);
    }

    expect(isChatIdle).not.toHaveBeenCalled();
  });

  it("stays idle when nothing is loading", async () => {
    const { isChatIdle } = mount({ loading: false });

    await advancePastSilence();

    expect(isChatIdle).not.toHaveBeenCalled();
  });

  it("stays idle without a backend-known chat id", async () => {
    const { isChatIdle } = mount({ chatId: "" });

    await advancePastSilence();

    expect(isChatIdle).not.toHaveBeenCalled();
  });

  it("does not probe a hidden tab", async () => {
    const { isChatIdle } = mount();
    setVisibility("hidden");

    await advancePastSilence();

    expect(isChatIdle).not.toHaveBeenCalled();
  });

  it("stops probing once loading goes away", async () => {
    const { isChatIdle, view } = mount({ idle: false });

    await advancePastSilence();
    const callsWhileLoading = isChatIdle.mock.calls.length;
    expect(callsWhileLoading).toBeGreaterThan(0);

    view.rerender({ loading: false });
    await advancePastSilence();

    expect(isChatIdle).toHaveBeenCalledTimes(callsWhileLoading);
  });

  it("re-checks a long silent run once per window, not every tick", async () => {
    const { isChatIdle } = mount({ idle: false });

    // The backend only heartbeats every 25s, so a slow think phase looks
    // silent for a long time; that must not turn into a poll every interval.
    await vi.advanceTimersByTimeAsync(LOADING_SILENCE_MS * 3);

    expect(isChatIdle.mock.calls.length).toBeLessThanOrEqual(3);
    expect(isChatIdle).toHaveBeenCalled();
  });
});
