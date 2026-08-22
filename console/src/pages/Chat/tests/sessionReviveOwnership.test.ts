import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import api, { type ChatHistory, type ChatSpec } from "../../../api";
import sessionApi from "../sessionApi";
import { isReviveUnchanged, prepareSessionRevive } from "../sessionRevive";
import { useTurnUsageStore } from "../turnUsageStore";

const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const history = { messages: [], status: "idle" } as unknown as ChatHistory;

beforeEach(async () => {
  vi.useFakeTimers();
  sessionApi.resetForTests();
  sessionApi.setActiveAgent("agent-a");
  vi.spyOn(api, "listChats").mockResolvedValue(
    [A, B].map(
      (id) =>
        ({
          id,
          name: id,
          session_id: `console:${id}`,
          user_id: "user",
          channel: "console",
          meta: {},
          status: "idle",
        }) as unknown as ChatSpec,
    ),
  );
  await sessionApi.getSessionList();
});

afterEach(() => {
  sessionApi.resetForTests();
  useTurnUsageStore.getState().setSnapshot(null);
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("session revival ownership", () => {
  it("does not apply a delayed revival after selecting another chat", async () => {
    let resolveA!: (value: ChatHistory) => void;
    const pending = new Promise<ChatHistory>((resolve) => {
      resolveA = resolve;
    });
    vi.spyOn(api, "getChat").mockImplementation((id) =>
      id === A ? pending : Promise.resolve(history),
    );
    let current = A;
    const isCurrent = () => current === A;
    const remount = vi.fn();
    const revive = prepareSessionRevive({
      sessionId: A,
      invalidate: (id) => sessionApi.invalidateConvertedCache(id),
      preload: (id) =>
        sessionApi.preloadSession(id, undefined, true, isCurrent),
      isCurrent,
      remount,
    });

    current = B;
    await sessionApi.getSession(B);
    const snapshot = { usage: { total_tokens: 42 }, context_usage: null };
    useTurnUsageStore.getState().setSnapshot(snapshot);
    resolveA(history);

    expect(await revive).toBe(false);
    expect(remount).not.toHaveBeenCalled();
    expect(useTurnUsageStore.getState().snapshot).toEqual(snapshot);
  });

  it("applies fresh history and remounts when the same chat is still active", async () => {
    vi.spyOn(api, "getChat").mockResolvedValue(history);
    const remount = vi.fn();
    const isCurrent = () => true;
    const result = await prepareSessionRevive({
      sessionId: A,
      invalidate: (id) => sessionApi.invalidateConvertedCache(id),
      preload: (id) =>
        sessionApi.preloadSession(id, undefined, true, isCurrent),
      isCurrent,
      remount,
    });
    expect(result).toBe(true);
    expect(remount).toHaveBeenCalledOnce();
  });

  it("recognises an unchanged chat across reads with regenerated ids", async () => {
    // Each read of the history API regenerates every message id; only
    // metadata.original_id (the stored memory id) is stable.
    let read = 0;
    const turn = (originalIds: string[], status = "idle") => {
      read += 1;
      return {
        status,
        messages: [
          ["user", "m-user"],
          ...originalIds.map((originalId) => ["assistant", originalId]),
        ].map(([role, originalId], index) => ({
          id: `read${read}-${index}`,
          role,
          type: "message",
          content: [{ type: "text", text: originalId }],
          metadata: { original_id: originalId },
        })),
      } as unknown as ChatHistory;
    };
    const getChat = vi.spyOn(api, "getChat");
    const load = async (history: ChatHistory) => {
      getChat.mockResolvedValueOnce(history);
      return (await sessionApi.preloadSession(A, undefined, true)).session;
    };

    const shown = (await load(turn(["m-a1", "m-a2"]))).messages;
    const same = await load(turn(["m-a1", "m-a2"]));
    const grown = await load(turn(["m-a1", "m-a2", "m-a3"]));
    const running = await load(turn(["m-a1", "m-a2"], "running"));

    expect(same.messages[0].id).not.toBe(shown[0].id);
    expect(isReviveUnchanged({ streaming: false, shown, fresh: same })).toBe(
      true,
    );
    expect(isReviveUnchanged({ streaming: false, shown, fresh: grown })).toBe(
      false,
    );
    expect(isReviveUnchanged({ streaming: false, shown, fresh: running })).toBe(
      false,
    );
  });
});
