import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  isStreamDead,
  useStreamRevive,
  STREAM_SILENCE_MS,
  STREAM_CHECK_INTERVAL_MS,
  HIDDEN_KILL_MS,
} from "./useStreamRevive";

const START = 1_000_000;

const setVisibility = (state: DocumentVisibilityState) => {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
};

/** Advance the fake clock (and with it Date.now()) plus any due timers. */
const advance = (ms: number) => {
  vi.advanceTimersByTime(ms);
};

/** jsdom has no PageTransitionEvent constructor; `persisted` is what matters. */
const pageShowEvent = (persisted: boolean) =>
  Object.assign(new Event("pageshow"), { persisted });

const hide = () => {
  setVisibility("hidden");
  document.dispatchEvent(new Event("visibilitychange"));
};

const show = () => {
  setVisibility("visible");
  document.dispatchEvent(new Event("visibilitychange"));
};

describe("isStreamDead", () => {
  it("never fires without a baseline timestamp", () => {
    expect(isStreamDead(0, 10_000_000)).toBe(false);
  });

  it("tolerates silence shorter than the heartbeat window", () => {
    expect(isStreamDead(1_000, 1_000 + STREAM_SILENCE_MS - 1)).toBe(false);
  });

  it("reports a stream silent for a full minute as dead", () => {
    expect(isStreamDead(1_000, 1_000 + STREAM_SILENCE_MS)).toBe(true);
  });
});

describe("useStreamRevive", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(START);
    setVisibility("visible");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const mount = (streaming: boolean) => {
    const revive = vi.fn();
    const lastEventAtRef = { current: 0 };
    const view = renderHook(
      (props: { streaming: boolean }) =>
        useStreamRevive({
          streaming: props.streaming,
          lastEventAtRef,
          revive,
        }),
      { initialProps: { streaming } },
    );
    return { revive, lastEventAtRef, view };
  };

  it("stamps a baseline when a run starts", () => {
    const { lastEventAtRef } = mount(true);
    expect(lastEventAtRef.current).toBe(START);
  });

  it("revives the moment the screen comes back on", () => {
    const { revive } = mount(true);

    hide();
    advance(HIDDEN_KILL_MS);
    show();

    // No waiting for the silence window: the answer's tail is fetched now.
    expect(revive).toHaveBeenCalledTimes(1);
    expect(Date.now()).toBe(START + HIDDEN_KILL_MS);
  });

  it("revives after a long screen-off too", () => {
    const { revive } = mount(true);

    hide();
    advance(10 * 60_000);
    expect(revive).not.toHaveBeenCalled();

    show();
    expect(revive).toHaveBeenCalledTimes(1);
  });

  it("leaves a quick tab flick alone", () => {
    const { revive } = mount(true);

    hide();
    advance(HIDDEN_KILL_MS - 1);
    show();

    expect(revive).not.toHaveBeenCalled();
  });

  it("still revives a quick flick whose stream had already died", () => {
    const { revive, lastEventAtRef } = mount(true);

    lastEventAtRef.current = Date.now() - STREAM_SILENCE_MS;
    hide();
    advance(HIDDEN_KILL_MS - 1);
    show();

    expect(revive).toHaveBeenCalledTimes(1);
  });

  it("revives a visible tab whose stream went silent", () => {
    const { revive } = mount(true);

    advance(STREAM_SILENCE_MS);

    expect(revive).toHaveBeenCalledTimes(1);
  });

  it("leaves a live stream alone", () => {
    const { revive, lastEventAtRef } = mount(true);

    // A chunk keeps arriving just inside every check window.
    for (let elapsed = 0; elapsed < STREAM_SILENCE_MS * 2; ) {
      advance(STREAM_CHECK_INTERVAL_MS);
      elapsed += STREAM_CHECK_INTERVAL_MS;
      lastEventAtRef.current = Date.now();
    }

    expect(revive).not.toHaveBeenCalled();
  });

  it("revives on a bfcache restore", () => {
    const { revive } = mount(true);

    window.dispatchEvent(pageShowEvent(true));

    expect(revive).toHaveBeenCalledTimes(1);
  });

  it("ignores the pageshow of a plain load", () => {
    const { revive } = mount(true);

    window.dispatchEvent(pageShowEvent(false));

    expect(revive).not.toHaveBeenCalled();
  });

  it("reloads an idle chat on wake, in case another channel wrote to it", () => {
    const { revive } = mount(false);

    hide();
    advance(HIDDEN_KILL_MS);
    show();

    expect(revive).toHaveBeenCalledTimes(1);
  });

  it("never treats an idle chat's silence as a dead stream", () => {
    const { revive } = mount(false);

    advance(STREAM_SILENCE_MS * 10);

    expect(revive).not.toHaveBeenCalled();
  });

  it("never revives a tab that is still hidden", () => {
    const { revive } = mount(true);

    hide();
    advance(STREAM_SILENCE_MS * 2);

    expect(revive).not.toHaveBeenCalled();
  });

  it("retries at most once per silence window", () => {
    const { revive } = mount(true);

    advance(STREAM_SILENCE_MS);
    expect(revive).toHaveBeenCalledTimes(1);

    advance(STREAM_SILENCE_MS - STREAM_CHECK_INTERVAL_MS);
    expect(revive).toHaveBeenCalledTimes(1);

    advance(STREAM_CHECK_INTERVAL_MS);
    expect(revive).toHaveBeenCalledTimes(2);
  });

  it("stops the silence watch once the run ends", () => {
    const { revive, view } = mount(true);

    view.rerender({ streaming: false });
    advance(STREAM_SILENCE_MS * 2);

    expect(revive).not.toHaveBeenCalled();
  });

  it("unsubscribes on unmount", () => {
    const { revive, view } = mount(true);

    view.unmount();
    hide();
    advance(STREAM_SILENCE_MS * 2);
    show();

    expect(revive).not.toHaveBeenCalled();
  });
});
