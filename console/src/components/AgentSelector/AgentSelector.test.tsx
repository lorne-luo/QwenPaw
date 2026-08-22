import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/common_setup";
import AgentSelector from "./index";
import { useAgentSessionActivityStore } from "@/stores/agentSessionActivityStore";
import { useSessionAttentionStore } from "@/stores/sessionAttentionStore";
import { useSessionListStore } from "@/stores/sessionListStore";

const mocks = vi.hoisted(() => ({
  setSelectedAgent: vi.fn(),
  setAgents: vi.fn(),
  refreshAgents: vi.fn(),
  toggleAgentEnabled: vi.fn(),
  setAgentPinned: vi.fn(),
  getSessionActivity: vi.fn(),
  navigate: vi.fn(),
  storeState: {
    selectedAgent: "default",
    agents: [] as Array<Record<string, unknown>>,
  },
}));

vi.mock("@/api/modules/agents", () => ({
  agentsApi: {
    toggleAgentEnabled: mocks.toggleAgentEnabled,
    setAgentPinned: mocks.setAgentPinned,
    getSessionActivity: mocks.getSessionActivity,
  },
}));

vi.mock("@/stores/agentStore", () => {
  const getState = () => ({
    ...mocks.storeState,
    setSelectedAgent: mocks.setSelectedAgent,
    setAgents: mocks.setAgents,
    refreshAgents: mocks.refreshAgents,
  });
  const useAgentStore = vi.fn(
    (selector?: (state: ReturnType<typeof getState>) => unknown) =>
      selector ? selector(getState()) : getState(),
  );
  return {
    useAgentStore: Object.assign(useAgentStore, {
      getState,
      subscribe: () => () => {},
    }),
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => mocks.navigate };
});

const agents = [
  {
    id: "default",
    name: "Default",
    enabled: true,
    description: "",
    workspace_dir: "",
    startup_status: "running",
    pinned: true,
  },
  {
    id: "agent-1",
    name: "Agent One",
    enabled: true,
    description: "desc",
    workspace_dir: "",
    startup_status: "running",
    pinned: false,
  },
  {
    id: "agent-2",
    name: "Agent Two",
    enabled: false,
    description: "",
    workspace_dir: "",
    startup_status: "disabled",
    pinned: false,
  },
];

describe("AgentSelector", () => {
  beforeEach(() => {
    mocks.storeState.selectedAgent = "default";
    mocks.storeState.agents = agents;
    mocks.refreshAgents.mockResolvedValue(undefined);
    mocks.getSessionActivity.mockResolvedValue({ agents: [] });
    localStorage.clear();
    useSessionListStore.setState({ sessions: [] });
    useAgentSessionActivityStore.setState({ sessionsByAgent: {} });
    useSessionAttentionStore.setState({
      seenFinishedAt: {},
      baselinedAgents: {},
    });
    mocks.toggleAgentEnabled.mockResolvedValue({
      success: true,
      agent_id: "agent-2",
      enabled: true,
    });
    mocks.setAgentPinned.mockResolvedValue({
      success: true,
      agent_id: "agent-1",
      pinned: true,
    });
  });

  afterEach(() => vi.clearAllMocks());

  it("refreshes the shared agent store on mount", async () => {
    renderWithProviders(<AgentSelector />);
    await waitFor(() => expect(mocks.refreshAgents).toHaveBeenCalledOnce());
  });

  it("does not render Select in collapsed mode", async () => {
    renderWithProviders(<AgentSelector collapsed />);
    await waitFor(() => expect(mocks.refreshAgents).toHaveBeenCalled());
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("switches agents from the collapsed menu and closes it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector collapsed />);
    const trigger = screen.getByRole("button", { name: "agent.selectAgent" });

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await user.click(await screen.findByRole("option", { name: /Agent One/ }));

    expect(mocks.setSelectedAgent).toHaveBeenCalledWith("agent-1");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("prevents selecting disabled agents and dismisses with Escape", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector collapsed />);
    const trigger = screen.getByRole("button", { name: "agent.selectAgent" });
    await user.click(trigger);

    await user.click(
      screen.getByRole("button", { name: "agent.disabledAgents" }),
    );
    const disabled = await screen.findByText("Agent Two");
    await user.click(disabled);
    expect(mocks.setSelectedAgent).not.toHaveBeenCalled();
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    screen.getByRole("option", { name: /Agent One/ }).focus();
    await user.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("shows disabled agents only after expanding the footer", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector />);

    await user.click(screen.getByRole("combobox"));
    expect(screen.queryByText("Agent Two")).not.toBeInTheDocument();

    const disabledHeader = screen.getByRole("button", {
      name: "agent.disabledAgents",
    });
    expect(disabledHeader).toHaveAttribute("aria-expanded", "false");
    await user.click(disabledHeader);

    expect(disabledHeader).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Agent Two")).toBeInTheDocument();
  });

  it("keeps the popup class and selection behavior", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector />);
    const selector = screen.getByRole("combobox");

    await user.click(selector);

    const popup = document.querySelector<HTMLElement>(
      '[class*="agentSelectorDropdown"]',
    );
    expect(popup).toBeInTheDocument();

    await user.click(screen.getByText("Agent One"));

    expect(mocks.setSelectedAgent).toHaveBeenCalledWith("agent-1");
  });

  it("closes with Escape, restores focus, and supports compact mode", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector compact />);
    const selector = screen.getByRole("combobox");

    expect(
      screen.queryByText("agent.currentWorkspace"),
    ).not.toBeInTheDocument();

    await user.click(selector);
    expect(
      document.querySelector('[class*="agentSelectorDropdown"]'),
    ).toBeInTheDocument();

    fireEvent.keyDown(selector, {
      key: "Escape",
      code: "Escape",
      keyCode: 27,
      which: 27,
    });

    await waitFor(() =>
      expect(selector).toHaveAttribute("aria-expanded", "false"),
    );
    expect(selector).toHaveFocus();
  });

  it("does not expose app-managed execution profiles in Chat", async () => {
    mocks.storeState.agents = [
      ...agents,
      {
        id: "datapaw",
        name: "QwenPaw-Data",
        enabled: true,
        pinned: true,
        description: "",
        workspace_dir: "",
        startup_status: "running",
        managed_by_app: "datapaw",
        available_in_chat: false,
      },
    ];
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector />);

    await user.click(screen.getByRole("combobox"));

    expect(screen.queryByText("QwenPaw-Data")).not.toBeInTheDocument();
  });

  it("leaves a persisted app-managed agent selection for default Chat", async () => {
    mocks.storeState.selectedAgent = "datapaw";
    mocks.storeState.agents = [
      ...agents,
      {
        id: "datapaw",
        name: "QwenPaw-Data",
        enabled: true,
        pinned: true,
        description: "",
        workspace_dir: "",
        startup_status: "running",
        managed_by_app: "datapaw",
        available_in_chat: false,
      },
    ];

    renderWithProviders(<AgentSelector />);

    await waitFor(() =>
      expect(mocks.setSelectedAgent).toHaveBeenCalledWith("default"),
    );
  });

  it("keeps a pinned disabled agent visible and lets it be enabled", async () => {
    const pinnedDisabledAgent = {
      id: "agent-3",
      name: "Pinned Disabled",
      enabled: false,
      pinned: true,
      description: "",
      workspace_dir: "",
      startup_status: "disabled",
    };
    const nextAgents = [...agents, pinnedDisabledAgent];
    mocks.storeState.agents = nextAgents;
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector />);

    await user.click(screen.getByRole("combobox"));
    expect(screen.getByText("Pinned Disabled")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "agent.enableAgent" }));

    expect(mocks.toggleAgentEnabled).toHaveBeenCalledWith("agent-3", true);
  });

  it("optimistically marks an enabled agent as starting", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector />);
    await user.click(screen.getByRole("combobox"));
    await user.click(
      screen.getByRole("button", { name: "agent.disabledAgents" }),
    );
    await user.click(screen.getByRole("button", { name: "agent.enableAgent" }));

    expect(mocks.toggleAgentEnabled).toHaveBeenCalledWith("agent-2", true);
    expect(mocks.setAgents).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({
          id: "agent-2",
          enabled: true,
          startup_status: "starting",
        }),
      ]),
    );
  });

  it("switches to default after disabling the selected agent", async () => {
    mocks.storeState.selectedAgent = "agent-1";
    const user = userEvent.setup();
    renderWithProviders(<AgentSelector />);
    await user.click(screen.getByRole("combobox"));
    await user.click(
      screen.getByRole("button", { name: "agent.disableAgent" }),
    );

    await waitFor(() => {
      expect(mocks.toggleAgentEnabled).toHaveBeenCalledWith("agent-1", false);
    });
    expect(mocks.setSelectedAgent).toHaveBeenCalledWith("default");
  });

  describe("session activity", () => {
    const FINISHED = "2026-10-03T08:00:00.000Z";
    const runningAgent = (id: string, name: string) => ({
      id,
      name,
      enabled: true,
      description: "",
      workspace_dir: "",
      startup_status: "running",
      pinned: false,
    });
    const activityAgents = [
      { ...runningAgent("default", "Default"), pinned: true },
      runningAgent("agent-a", "Agent A"),
      runningAgent("agent-b", "Agent B"),
      runningAgent("agent-c", "Agent C"),
    ];

    function rowIndicator(name: string) {
      const row = screen
        .getAllByText(name)
        .map((element) => element.closest(".ant-select-item-option"))
        .find(Boolean);
      return row?.querySelector('[role="status"]');
    }

    it("refreshes on open and colors other agents from their own sessions", async () => {
      mocks.storeState.selectedAgent = "agent-c";
      mocks.storeState.agents = activityAgents;
      useSessionAttentionStore.setState({
        baselinedAgents: { "agent-b": true },
      });
      let resolveActivity!: (value: unknown) => void;
      mocks.getSessionActivity.mockReturnValue(
        new Promise((resolve) => {
          resolveActivity = resolve;
        }),
      );
      const user = userEvent.setup();
      renderWithProviders(<AgentSelector />);

      await user.click(screen.getByRole("combobox"));

      // The list is usable before the activity request resolves.
      expect(mocks.getSessionActivity).toHaveBeenCalledOnce();
      expect(rowIndicator("Agent A")).toHaveAttribute("data-activity", "none");
      expect(rowIndicator("Agent B")).toHaveAttribute("data-activity", "none");

      resolveActivity({
        agents: [
          { agent_id: "agent-a", sessions: [{ id: "a1", status: "running" }] },
          {
            agent_id: "agent-b",
            sessions: [
              { id: "b1", status: "idle", last_finished_at: FINISHED },
            ],
          },
          // The selected agent follows the sidebar list, not this response.
          { agent_id: "agent-c", sessions: [{ id: "c1", status: "running" }] },
        ],
      });

      await waitFor(() =>
        expect(rowIndicator("Agent A")).toHaveAttribute(
          "data-activity",
          "generating",
        ),
      );
      expect(rowIndicator("Agent B")).toHaveAttribute(
        "data-activity",
        "unseen",
      );
      expect(rowIndicator("Agent C")).toHaveAttribute("data-activity", "none");
      expect(mocks.getSessionActivity).toHaveBeenCalledOnce();
    });

    it("keeps the collapsed list usable when the activity request fails", async () => {
      vi.spyOn(console, "warn").mockImplementation(() => {});
      mocks.getSessionActivity.mockRejectedValue(new Error("offline"));
      const user = userEvent.setup();
      renderWithProviders(<AgentSelector collapsed />);

      await user.click(
        screen.getByRole("button", { name: "agent.selectAgent" }),
      );
      expect(mocks.getSessionActivity).toHaveBeenCalledOnce();
      await user.click(
        await screen.findByRole("option", { name: /Agent One/ }),
      );

      expect(mocks.setSelectedAgent).toHaveBeenCalledWith("agent-1");
      vi.mocked(console.warn).mockRestore();
    });
  });
});
