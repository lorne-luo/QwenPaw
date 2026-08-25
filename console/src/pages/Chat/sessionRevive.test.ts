import { describe, expect, it, vi } from "vitest";
import { prepareSessionRevive } from "./sessionRevive";

describe("prepareSessionRevive", () => {
  it("keeps the current UI mounted until fresh session data is ready", async () => {
    let finishPreload: (() => void) | undefined;
    const preload = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishPreload = resolve;
        }),
    );
    const invalidate = vi.fn();
    const remount = vi.fn();

    const result = prepareSessionRevive({
      sessionId: "chat-1",
      invalidate,
      preload,
      isCurrent: () => true,
      remount,
    });

    expect(invalidate).toHaveBeenCalledWith("chat-1");
    expect(preload).toHaveBeenCalledWith("chat-1");
    expect(remount).not.toHaveBeenCalled();

    finishPreload?.();
    await expect(result).resolves.toBe(true);
    expect(remount).toHaveBeenCalledTimes(1);
  });

  it("preserves the current UI when preloading fails", async () => {
    const remount = vi.fn();

    const result = await prepareSessionRevive({
      sessionId: "chat-1",
      invalidate: vi.fn(),
      preload: vi.fn().mockRejectedValue(new Error("offline")),
      isCurrent: () => true,
      remount,
    });

    expect(result).toBe(false);
    expect(remount).not.toHaveBeenCalled();
  });

  it("does not remount a session that changed during preload", async () => {
    const remount = vi.fn();

    const result = await prepareSessionRevive({
      sessionId: "chat-1",
      invalidate: vi.fn(),
      preload: vi.fn().mockResolvedValue(undefined),
      isCurrent: () => false,
      remount,
    });

    expect(result).toBe(false);
    expect(remount).not.toHaveBeenCalled();
  });
});
