import { describe, expect, it, vi } from "vitest";
import {
  isReviveUnchanged,
  isSameConversation,
  prepareSessionRevive,
  isRevivedViewReady,
} from "./sessionRevive";

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

describe("prepareSessionRevive unchanged check", () => {
  it("skips the remount when the fresh data matches the screen", async () => {
    const fresh = { messages: 3 };
    const isUnchanged = vi.fn(() => true);
    const remount = vi.fn();

    const result = await prepareSessionRevive({
      sessionId: "chat-1",
      invalidate: vi.fn(),
      preload: vi.fn().mockResolvedValue(fresh),
      isCurrent: () => true,
      isUnchanged,
      remount,
    });

    expect(result).toBe(false);
    expect(isUnchanged).toHaveBeenCalledWith(fresh);
    expect(remount).not.toHaveBeenCalled();
  });

  it("remounts with the fresh data when it differs from the screen", async () => {
    const fresh = { messages: 4 };
    const remount = vi.fn();

    const result = await prepareSessionRevive({
      sessionId: "chat-1",
      invalidate: vi.fn(),
      preload: vi.fn().mockResolvedValue(fresh),
      isCurrent: () => true,
      isUnchanged: () => false,
      remount,
    });

    expect(result).toBe(true);
    expect(remount).toHaveBeenCalledWith(fresh);
  });

  it("does not ask about changes for a session that is no longer current", async () => {
    const isUnchanged = vi.fn(() => false);

    const result = await prepareSessionRevive({
      sessionId: "chat-1",
      invalidate: vi.fn(),
      preload: vi.fn().mockResolvedValue({}),
      isCurrent: () => false,
      isUnchanged,
      remount: vi.fn(),
    });

    expect(result).toBe(false);
    expect(isUnchanged).not.toHaveBeenCalled();
  });
});

describe("isSameConversation", () => {
  const user = (originalId: string, id = `ui-${originalId}`) => ({
    id,
    role: "user",
    cards: [
      {
        code: "AgentScopeRuntimeRequestCard",
        data: { input: [{ metadata: { original_id: originalId } }] },
      },
    ],
  });
  const reply = (originalIds: string[], id = `ai-${originalIds.join("")}`) => ({
    id,
    role: "assistant",
    cards: [
      {
        code: "AgentScopeRuntimeResponseCard",
        data: {
          output: originalIds.map((originalId) => ({
            metadata: { original_id: originalId },
          })),
        },
      },
    ],
  });
  const shown = [user("u1"), reply(["a1", "a2"]), user("u2"), reply(["a3"])];

  it("treats the same persisted conversation as the same", () => {
    // The history API regenerates every message id on each read.
    const fresh = [
      user("u1", "new-1"),
      reply(["a1", "a2"], "new-2"),
      user("u2", "new-3"),
      reply(["a3"], "new-4"),
    ];
    expect(isSameConversation(shown, fresh)).toBe(true);
  });

  it("detects an extra message", () => {
    expect(isSameConversation(shown, [...shown, user("u3")])).toBe(false);
  });

  it("detects a removed message", () => {
    expect(isSameConversation(shown, shown.slice(0, 3))).toBe(false);
  });

  it("detects a different user message", () => {
    const fresh = [shown[0], shown[1], user("u9"), shown[3]];
    expect(isSameConversation(shown, fresh)).toBe(false);
  });

  it("detects a reply that grew in place", () => {
    const fresh = [shown[0], shown[1], shown[2], reply(["a3", "a4"])];
    expect(isSameConversation(shown, fresh)).toBe(false);
  });

  it("detects a role mismatch at the same position", () => {
    const fresh = [shown[0], shown[1], reply(["u2"]), shown[3]];
    expect(isSameConversation(shown, fresh)).toBe(false);
  });

  it("never matches a message without a persisted identity", () => {
    const live = {
      id: "live",
      role: "user",
      cards: [{ data: { input: [{}] } }],
    };
    expect(isSameConversation([live], [live])).toBe(false);
  });

  it("treats two empty conversations as the same", () => {
    expect(isSameConversation([], [])).toBe(true);
  });

  describe("isReviveUnchanged", () => {
    it("keeps the runtime for an idle, unchanged chat", () => {
      expect(
        isReviveUnchanged({
          streaming: false,
          shown,
          fresh: { generating: false, messages: [...shown] },
        }),
      ).toBe(true);
    });

    it("remounts while this tab is streaming", () => {
      expect(
        isReviveUnchanged({
          streaming: true,
          shown,
          fresh: { generating: false, messages: [...shown] },
        }),
      ).toBe(false);
    });

    it("remounts while the backend reports a run in progress", () => {
      expect(
        isReviveUnchanged({
          streaming: false,
          shown,
          fresh: { generating: true, messages: [...shown] },
        }),
      ).toBe(false);
    });

    it("remounts when the history changed", () => {
      expect(
        isReviveUnchanged({
          streaming: false,
          shown,
          fresh: { generating: false, messages: [...shown, user("u3")] },
        }),
      ).toBe(false);
    });
  });
});

describe("isRevivedViewReady", () => {
  const user = { role: "user", cards: [{ data: { input: [{}] } }] };
  const reply = (status: string, output: unknown[]) => ({
    role: "assistant",
    cards: [{ data: { status, output } }],
  });

  it("is not ready before any history has rendered", () => {
    expect(isRevivedViewReady([], false)).toBe(false);
  });

  it("is ready once a finished conversation has rendered", () => {
    expect(isRevivedViewReady([user, reply("completed", [{}])], false)).toBe(
      true,
    );
    expect(isRevivedViewReady([user], false)).toBe(true);
  });

  it("waits while a running reply has not been attached yet", () => {
    expect(isRevivedViewReady([user], true)).toBe(false);
  });

  it("waits while the attached reply has no replayed output", () => {
    expect(isRevivedViewReady([user, reply("in_progress", [])], true)).toBe(
      false,
    );
    expect(isRevivedViewReady([user, reply("created", [])], true)).toBe(false);
  });

  it("is ready once the running reply shows replayed output", () => {
    expect(isRevivedViewReady([user, reply("in_progress", [{}])], true)).toBe(
      true,
    );
  });
});
