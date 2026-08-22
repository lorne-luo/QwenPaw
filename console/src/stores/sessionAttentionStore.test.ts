import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  hasUnseenCompletion,
  sessionAttentionKey,
  useSessionAttentionStore,
  type AttentionSession,
} from "./sessionAttentionStore";

const session = {
  id: "local-id",
  realId: "chat-1",
  lastFinishedAt: "2026-08-25T08:00:00.000Z",
};

describe("sessionAttentionStore", () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionAttentionStore.setState({
      seenFinishedAt: {},
      baselinedAgents: {},
    });
  });

  const unseen = (agentId: string, target: AttentionSession) =>
    hasUnseenCompletion(
      useSessionAttentionStore.getState().seenFinishedAt,
      agentId,
      target,
    );

  it("uses the backend id so local id remapping keeps the read marker", () => {
    expect(sessionAttentionKey("agent-a", session)).toBe("agent-a:chat-1");
  });

  it("baselines existing completions without marking old history unseen", () => {
    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [session]);

    expect(
      hasUnseenCompletion(
        useSessionAttentionStore.getState().seenFinishedAt,
        "agent-a",
        session,
      ),
    ).toBe(false);
  });

  it("marks a newer completion unseen until the session is viewed", () => {
    const store = useSessionAttentionStore.getState();
    store.initializeSessions("agent-a", [session]);
    const rerun = {
      ...session,
      lastFinishedAt: "2026-08-25T09:00:00.000Z",
    };

    expect(
      hasUnseenCompletion(
        useSessionAttentionStore.getState().seenFinishedAt,
        "agent-a",
        rerun,
      ),
    ).toBe(true);

    useSessionAttentionStore.getState().markSeen("agent-a", rerun);
    expect(
      hasUnseenCompletion(
        useSessionAttentionStore.getState().seenFinishedAt,
        "agent-a",
        rerun,
      ),
    ).toBe(false);
  });

  it("keeps read markers separate for each agent", () => {
    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [session]);

    expect(
      hasUnseenCompletion(
        useSessionAttentionStore.getState().seenFinishedAt,
        "agent-b",
        session,
      ),
    ).toBe(false);
  });

  it("marks a session first observed already finished as unseen", () => {
    const store = useSessionAttentionStore.getState();
    store.initializeSessions("agent-a", [session]);
    const channelReply = {
      id: "chat-2",
      lastFinishedAt: "2026-08-25T10:00:00.000Z",
    };

    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [channelReply, session]);

    expect(unseen("agent-a", channelReply)).toBe(true);
    expect(unseen("agent-a", session)).toBe(false);
  });

  it("baselines an agent even when its first observation is empty", () => {
    useSessionAttentionStore.getState().initializeSessions("agent-a", []);
    expect(useSessionAttentionStore.getState().baselinedAgents).toEqual({
      "agent-a": true,
    });

    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [session]);
    expect(unseen("agent-a", session)).toBe(true);
  });

  it("does not notify subscribers when nothing new is observed", () => {
    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [session]);
    const listener = vi.fn();
    const unsubscribe = useSessionAttentionStore.subscribe(listener);

    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [session]);
    useSessionAttentionStore
      .getState()
      .initializeAgentSessions({ "agent-a": [session] });

    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("baselines legacy storage without baselinedAgents instead of flooding", async () => {
    localStorage.setItem(
      "qwenpaw-session-attention",
      JSON.stringify({
        state: { seenFinishedAt: { "agent-a:chat-1": session.lastFinishedAt } },
        version: 0,
      }),
    );
    await useSessionAttentionStore.persist.rehydrate();
    expect(useSessionAttentionStore.getState().baselinedAgents).toEqual({});

    const neverObserved = {
      id: "chat-9",
      lastFinishedAt: "2026-08-24T08:00:00.000Z",
    };
    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [session, neverObserved]);

    expect(unseen("agent-a", session)).toBe(false);
    expect(unseen("agent-a", neverObserved)).toBe(false);
    expect(
      JSON.parse(localStorage.getItem("qwenpaw-session-attention") ?? "{}")
        .state.baselinedAgents,
    ).toEqual({ "agent-a": true });
  });

  it("initializes many agents in one write and keeps them isolated", () => {
    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-a", [session]);
    const listener = vi.fn();
    const unsubscribe = useSessionAttentionStore.subscribe(listener);
    const sameChatId = {
      id: "chat-2",
      lastFinishedAt: "2026-08-25T10:00:00.000Z",
    };

    useSessionAttentionStore.getState().initializeAgentSessions({
      "agent-a": [session, sameChatId],
      "agent-b": [sameChatId],
      "agent-c": [],
    });

    expect(listener).toHaveBeenCalledTimes(1);
    // agent-a was already baselined, so its new finished session is unseen;
    // agent-b sees the same chat id for the first time and baselines it.
    expect(unseen("agent-a", sameChatId)).toBe(true);
    expect(unseen("agent-b", sameChatId)).toBe(false);
    expect(useSessionAttentionStore.getState().baselinedAgents).toEqual({
      "agent-a": true,
      "agent-b": true,
      "agent-c": true,
    });
    unsubscribe();
  });
});
