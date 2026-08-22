import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { useAgentStore } from "@/stores/agentStore";
import { useAgentSessionActivityStore } from "@/stores/agentSessionActivityStore";
import { useSessionAttentionStore } from "@/stores/sessionAttentionStore";
import {
  useSessionListStore,
  type ExtendedSession,
} from "@/stores/sessionListStore";
import { useAgentActivityMap } from "./useAgentActivity";

const FINISHED = "2026-10-03T08:00:00.000Z";

function renderMap(path = "/chat") {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
  );
  return renderHook(() => useAgentActivityMap(), { wrapper });
}

const listSession = (id: string, extra: Partial<ExtendedSession> = {}) =>
  ({ id, name: id, messages: [], ...extra }) as ExtendedSession;

describe("useAgentActivityMap", () => {
  beforeEach(() => {
    localStorage.clear();
    useAgentStore.setState({ selectedAgent: "agent-c" });
    useSessionListStore.setState({ sessions: [] });
    useAgentSessionActivityStore.setState({ sessionsByAgent: {} });
    useSessionAttentionStore.setState({
      seenFinishedAt: {},
      baselinedAgents: {},
    });
  });

  it("derives other agents from the activity store", () => {
    useAgentSessionActivityStore.setState({
      sessionsByAgent: {
        "agent-a": [{ id: "c1", status: "running" }],
        "agent-b": [{ id: "c2", status: "idle", lastFinishedAt: FINISHED }],
        "agent-d": [{ id: "c3", status: "idle", lastFinishedAt: FINISHED }],
      },
    });
    useSessionAttentionStore.setState({
      seenFinishedAt: { "agent-b:c2": null, "agent-d:c3": FINISHED },
    });

    const { result } = renderMap();

    expect(result.current).toEqual({
      "agent-a": "generating",
      "agent-b": "unseen",
      "agent-d": undefined,
      "agent-c": undefined,
    });
  });

  it("derives the selected agent only from the sidebar session list", () => {
    useAgentSessionActivityStore.setState({
      sessionsByAgent: { "agent-c": [{ id: "stale", status: "running" }] },
    });
    useSessionListStore.setState({
      sessions: [listSession("local-1", { realId: "c9", generating: true })],
    });

    const { result } = renderMap();

    expect(result.current["agent-c"]).toBe("generating");

    useSessionListStore.setState({ sessions: [] });
    expect(renderMap().result.current["agent-c"]).toBeUndefined();
  });

  it("does not count the visible current session of the selected agent", () => {
    useSessionListStore.setState({
      sessions: [
        listSession("c1", { status: "idle", lastFinishedAt: FINISHED }),
      ],
    });
    useSessionAttentionStore.setState({
      seenFinishedAt: { "agent-c:c1": null },
    });

    expect(renderMap("/chat/c1").result.current["agent-c"]).toBeUndefined();
    expect(renderMap("/chat/other").result.current["agent-c"]).toBe("unseen");
  });
});
