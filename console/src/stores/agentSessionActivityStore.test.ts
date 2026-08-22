import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentSessionActivityResponse } from "../api/types/agents";

const mocks = vi.hoisted(() => ({
  getSessionActivity: vi.fn(),
  selectedAgent: "agent-a",
}));

vi.mock("../api/modules/agents", () => ({
  agentsApi: { getSessionActivity: mocks.getSessionActivity },
}));

vi.mock("./agentStore", () => ({
  useAgentStore: {
    getState: () => ({ selectedAgent: mocks.selectedAgent }),
  },
}));

import { useAgentSessionActivityStore } from "./agentSessionActivityStore";
import {
  hasUnseenCompletion,
  useSessionAttentionStore,
} from "./sessionAttentionStore";

const FINISHED = "2026-10-03T08:00:00.000Z";

const response: AgentSessionActivityResponse = {
  agents: [
    {
      agent_id: "agent-a",
      sessions: [
        { id: "c1", status: "idle", counted: true, last_finished_at: FINISHED },
      ],
    },
    {
      agent_id: "agent-b",
      sessions: [
        { id: "c2", status: "running", counted: true },
        { id: "c3", status: "idle", counted: true, last_finished_at: FINISHED },
      ],
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("agentSessionActivityStore", () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.selectedAgent = "agent-a";
    useAgentSessionActivityStore.setState({ sessionsByAgent: {} });
    useSessionAttentionStore.setState({
      seenFinishedAt: {},
      baselinedAgents: {},
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it("maps the response per agent and baselines it in one write", async () => {
    mocks.getSessionActivity.mockResolvedValue(response);
    const attentionWrites = vi.fn();
    const unsubscribe = useSessionAttentionStore.subscribe(attentionWrites);

    await useAgentSessionActivityStore.getState().refresh();
    unsubscribe();

    expect(useAgentSessionActivityStore.getState().sessionsByAgent).toEqual({
      "agent-a": [
        { id: "c1", status: "idle", counted: true, lastFinishedAt: FINISHED },
      ],
      "agent-b": [
        { id: "c2", status: "running", counted: true, lastFinishedAt: null },
        { id: "c3", status: "idle", counted: true, lastFinishedAt: FINISHED },
      ],
    });
    expect(attentionWrites).toHaveBeenCalledTimes(1);
    expect(useSessionAttentionStore.getState().baselinedAgents).toEqual({
      "agent-a": true,
      "agent-b": true,
    });
  });

  it("baselines sessions that never color the agent, so the sidebar does not flood them", async () => {
    mocks.getSessionActivity.mockResolvedValue({
      agents: [
        {
          agent_id: "agent-b",
          sessions: [
            {
              id: "b1",
              status: "idle",
              counted: true,
              last_finished_at: FINISHED,
            },
            {
              id: "cron-1",
              status: "idle",
              counted: false,
              last_finished_at: FINISHED,
            },
          ],
        },
      ],
    } satisfies AgentSessionActivityResponse);

    // agent-b is first observed through the agent list ...
    await useAgentSessionActivityStore.getState().refresh();
    // ... and only later through its full sidebar list (cron chats included).
    const cron = { id: "cron-1", lastFinishedAt: FINISHED };
    useSessionAttentionStore
      .getState()
      .initializeSessions("agent-b", [
        { id: "b1", lastFinishedAt: FINISHED },
        cron,
      ]);

    expect(
      hasUnseenCompletion(
        useSessionAttentionStore.getState().seenFinishedAt,
        "agent-b",
        cron,
      ),
    ).toBe(false);
  });

  it("shares one in-flight request between overlapping calls", async () => {
    const pending = deferred<AgentSessionActivityResponse>();
    mocks.getSessionActivity.mockReturnValue(pending.promise);

    const first = useAgentSessionActivityStore.getState().refresh();
    const second = useAgentSessionActivityStore.getState().refresh();
    expect(mocks.getSessionActivity).toHaveBeenCalledTimes(1);

    pending.resolve(response);
    await Promise.all([first, second]);

    mocks.getSessionActivity.mockResolvedValue(response);
    await useAgentSessionActivityStore.getState().refresh();
    expect(mocks.getSessionActivity).toHaveBeenCalledTimes(2);
  });

  it("keeps the last known state when a request fails", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    mocks.getSessionActivity.mockResolvedValueOnce(response);
    await useAgentSessionActivityStore.getState().refresh();
    const known = useAgentSessionActivityStore.getState().sessionsByAgent;

    mocks.getSessionActivity.mockRejectedValueOnce(new Error("offline"));
    await expect(
      useAgentSessionActivityStore.getState().refresh(),
    ).resolves.toBeUndefined();

    expect(useAgentSessionActivityStore.getState().sessionsByAgent).toBe(known);
  });

  it("marks the selected agent's visible current session seen", async () => {
    useSessionAttentionStore.setState({
      seenFinishedAt: { "agent-a:c1": null, "agent-b:c3": null },
      baselinedAgents: { "agent-a": true, "agent-b": true },
    });
    mocks.getSessionActivity.mockResolvedValue(response);

    await useAgentSessionActivityStore.getState().refresh("c1");

    const { seenFinishedAt } = useSessionAttentionStore.getState();
    expect(seenFinishedAt["agent-a:c1"]).toBe(FINISHED);
    // Opening the list never marks other sessions seen.
    expect(
      hasUnseenCompletion(seenFinishedAt, "agent-b", {
        id: "c3",
        lastFinishedAt: FINISHED,
      }),
    ).toBe(true);
  });

  it("does not mark the current session seen while the tab is hidden", async () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    useSessionAttentionStore.setState({
      seenFinishedAt: { "agent-a:c1": null },
      baselinedAgents: { "agent-a": true },
    });
    mocks.getSessionActivity.mockResolvedValue(response);

    await useAgentSessionActivityStore.getState().refresh("c1");

    expect(
      useSessionAttentionStore.getState().seenFinishedAt["agent-a:c1"],
    ).toBe(null);
  });
});
