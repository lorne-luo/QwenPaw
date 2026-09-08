import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import api, { type ChatHistory, type ChatSpec } from "../../../api";
import sessionApi from "../sessionApi";
import { prepareSessionRevive } from "../sessionRevive";
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
});
