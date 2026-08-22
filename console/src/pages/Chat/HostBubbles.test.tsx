/**
 * HostBubbles.tsx — response bubble label.
 *
 * The bubble header used to read the welcome nick ("QwenPaw") for every
 * agent, so a reply gave no hint which agent produced it.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentScopeRuntimeRunStatus } from "@agentscope-ai/chat/lib/AgentScopeRuntimeWebUI/core/AgentScopeRuntime/types";
import { HostResponseCard } from "./HostBubbles";
import { useAgentStore } from "@/stores/agentStore";
import type { AgentSummary } from "@/api/types/agents";

vi.mock(
  "@agentscope-ai/chat/lib/AgentScopeRuntimeWebUI/core/Context/ChatAnywhereOptionsContext",
  () => ({
    useChatAnywhereOptions: (selector: (options: unknown) => unknown) =>
      selector({ welcome: { avatar: "/qwenpaw.png", nick: "QwenPaw" } }),
  }),
);

vi.mock(
  "@agentscope-ai/chat/lib/AgentScopeRuntimeWebUI/core/AgentScopeRuntime/Response/Actions",
  () => ({ default: () => null }),
);

const agent = (id: string, name: string): AgentSummary => ({
  id,
  name,
  description: "",
  workspace_dir: "/tmp",
  enabled: true,
  backend: "qwenpaw",
});

const response = {
  id: "resp-1",
  status: AgentScopeRuntimeRunStatus.Completed,
  created_at: 0,
  output: [],
};

describe("HostResponseCard nick", () => {
  beforeEach(() => {
    useAgentStore.setState({
      selectedAgent: "reviewer",
      agents: [
        agent("default", "Default Agent"),
        agent("reviewer", "Reviewer"),
      ],
    });
  });

  it("labels the bubble with the selected agent's name", () => {
    render(<HostResponseCard data={response as never} />);

    expect(screen.getByText("Reviewer")).toBeInTheDocument();
    expect(screen.queryByText("QwenPaw")).not.toBeInTheDocument();
  });

  it("falls back to the welcome nick when the agent is unknown", () => {
    useAgentStore.setState({ selectedAgent: "gone", agents: [] });

    render(<HostResponseCard data={response as never} />);

    expect(screen.getByText("QwenPaw")).toBeInTheDocument();
  });
});
