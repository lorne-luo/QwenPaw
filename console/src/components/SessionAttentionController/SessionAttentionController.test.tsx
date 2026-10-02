import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { useAgentStore } from "@/stores/agentStore";
import { useSessionAttentionStore } from "@/stores/sessionAttentionStore";
import {
  useSessionListStore,
  type ExtendedSession,
} from "@/stores/sessionListStore";
import { SessionAttentionController } from ".";

const FINISHED = "2026-10-03T08:00:00.000Z";

const listSession = (id: string, extra: Partial<ExtendedSession> = {}) =>
  ({
    id,
    name: id,
    messages: [],
    status: "idle",
    lastFinishedAt: FINISHED,
    ...extra,
  }) as ExtendedSession;

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionAttentionController />
    </MemoryRouter>,
  );
}

describe("SessionAttentionController", () => {
  beforeEach(() => {
    localStorage.clear();
    useAgentStore.setState({ selectedAgent: "agent-a" });
    useSessionListStore.setState({ sessions: [] });
    useSessionAttentionStore.setState({
      seenFinishedAt: {},
      baselinedAgents: {},
    });
  });

  it("marks the visible session seen without the sidebar list", () => {
    useSessionAttentionStore.setState({
      seenFinishedAt: { "agent-a:c1": null, "agent-a:c2": null },
      baselinedAgents: { "agent-a": true },
    });
    useSessionListStore.setState({
      sessions: [listSession("c1"), listSession("c2")],
    });

    renderAt("/chat/c1");

    const { seenFinishedAt } = useSessionAttentionStore.getState();
    expect(seenFinishedAt["agent-a:c1"]).toBe(FINISHED);
    expect(seenFinishedAt["agent-a:c2"]).toBeNull();
  });

  it("does not baseline an agent from a list that has not loaded yet", () => {
    useSessionListStore.setState({
      sessions: [
        listSession("1785114733908-0l0jmai", { lastFinishedAt: null }),
      ],
    });

    renderAt("/chat");

    expect(useSessionAttentionStore.getState().baselinedAgents).toEqual({});
  });

  it("baselines the first loaded list so old history stays seen", () => {
    useSessionListStore.setState({ sessions: [listSession("c1")] });

    renderAt("/chat");

    expect(useSessionAttentionStore.getState()).toMatchObject({
      seenFinishedAt: { "agent-a:c1": FINISHED },
      baselinedAgents: { "agent-a": true },
    });
  });
});
