import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, waitFor, screen } from "@testing-library/react";

import { agentsApi } from "@/api/modules/agents";
import { harnessApi } from "@/api/modules/harness";
import { renderWithProviders } from "@/test/common_setup";
import HarnessModelSelector from "./HarnessModelSelector";

const { mockUpdateAgent, mockUseAgentStore } = vi.hoisted(() => ({
  mockUpdateAgent: vi.fn(),
  mockUseAgentStore: vi.fn(),
}));

vi.mock("@/api/modules/agents", () => ({
  agentsApi: {
    updateBackendSettings: vi.fn(),
  },
}));

vi.mock("@/api/modules/harness", () => ({
  harnessApi: {
    listModels: vi.fn(),
  },
}));

vi.mock("@/stores/agentStore", () => ({
  useAgentStore: mockUseAgentStore,
}));

const models = [
  {
    id: "codex-mini",
    name: "Codex Mini",
    description: "",
    is_default: false,
    reasoning_efforts: ["low"],
    default_reasoning_effort: "low",
  },
  {
    id: "gpt-codex",
    name: "GPT Codex",
    description: "",
    is_default: true,
    reasoning_efforts: ["medium", "high"],
    default_reasoning_effort: "medium",
  },
];

function setupAgent(backendModel: string | null) {
  mockUseAgentStore.mockReturnValue({
    selectedAgent: "codex-agent",
    agents: [
      {
        id: "codex-agent",
        backend: "codex",
        backend_model: backendModel,
      },
    ],
    updateAgent: mockUpdateAgent,
  });
}

describe("HarnessModelSelector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupAgent(null);
    vi.mocked(harnessApi.listModels).mockResolvedValue({ models });
    vi.mocked(agentsApi.updateBackendSettings).mockResolvedValue({
      id: "codex-agent",
      name: "Codex Agent",
    });
  });

  it("keeps Antigravity CLI default without saving a model on mount", async () => {
    vi.mocked(harnessApi.listModels).mockResolvedValue({
      models: [{ ...models[0], id: "", name: "CLI default", is_default: true }],
    });
    renderWithProviders(<HarnessModelSelector providerId="antigravity" />);
    await screen.findByText("CLI default");
    expect(agentsApi.updateBackendSettings).not.toHaveBeenCalled();
  });

  it.each([
    ["gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)", [], false],
    ["", "Antigravity default", ["low", "medium", "high"], true],
  ])(
    "shows a saved Antigravity effort only where it applies (%s)",
    async (id, name, efforts, shown) => {
      // A listed Antigravity model id already names its level and the
      // backend ignores the saved effort, so it must not look applied.
      mockUseAgentStore.mockReturnValue({
        selectedAgent: "agy-agent",
        agents: [
          {
            id: "agy-agent",
            backend: "antigravity",
            backend_model: id || null,
            backend_reasoning_effort: "high",
          },
        ],
        updateAgent: mockUpdateAgent,
      });
      vi.mocked(harnessApi.listModels).mockResolvedValue({
        models: [
          {
            id,
            name,
            description: "",
            is_default: !id,
            reasoning_efforts: efforts,
            default_reasoning_effort: null,
          },
        ],
      });

      renderWithProviders(<HarnessModelSelector providerId="antigravity" />);
      fireEvent.click(await screen.findByText(name));

      await screen.findByText("agent.backend.reasoningEffort");
      expect(screen.queryByText("high") !== null).toBe(shown);
      expect(agentsApi.updateBackendSettings).not.toHaveBeenCalled();
    },
  );

  it("persists the backend default model on first load", async () => {
    renderWithProviders(<HarnessModelSelector providerId="codex" />);

    await waitFor(() =>
      expect(agentsApi.updateBackendSettings).toHaveBeenCalledWith(
        "codex-agent",
        {
          model: "gpt-codex",
          reasoning_effort: "medium",
        },
      ),
    );
    expect(mockUpdateAgent).toHaveBeenCalledWith("codex-agent", {
      backend_model: "gpt-codex",
      backend_reasoning_effort: "medium",
    });
  });

  it("uses the first model when the backend has no declared default", async () => {
    vi.mocked(harnessApi.listModels).mockResolvedValue({
      models: models.map((item) => ({ ...item, is_default: false })),
    });

    renderWithProviders(<HarnessModelSelector providerId="codex" />);

    await waitFor(() =>
      expect(agentsApi.updateBackendSettings).toHaveBeenCalledWith(
        "codex-agent",
        {
          model: "codex-mini",
          reasoning_effort: "low",
        },
      ),
    );
  });

  it("sends an explicit null when the model has no default effort", async () => {
    // The PATCH endpoint only touches fields present in the body and
    // JSON.stringify drops undefined, so a clear must be an explicit
    // null or the server silently keeps the previous effort.
    vi.mocked(harnessApi.listModels).mockResolvedValue({
      models: [
        {
          ...models[1],
          reasoning_efforts: [],
          default_reasoning_effort: null,
        },
      ],
    });

    renderWithProviders(<HarnessModelSelector providerId="codex" />);

    await waitFor(() =>
      expect(agentsApi.updateBackendSettings).toHaveBeenCalledWith(
        "codex-agent",
        {
          model: "gpt-codex",
          reasoning_effort: null,
        },
      ),
    );
  });

  it("does not overwrite an existing model selection", async () => {
    setupAgent("codex-mini");

    renderWithProviders(<HarnessModelSelector providerId="codex" />);

    await waitFor(() => expect(harnessApi.listModels).toHaveBeenCalledOnce());
    expect(agentsApi.updateBackendSettings).not.toHaveBeenCalled();
  });

  it("does not save a model when the backend returns an empty list", async () => {
    vi.mocked(harnessApi.listModels).mockResolvedValue({ models: [] });

    renderWithProviders(<HarnessModelSelector providerId="codex" />);

    await waitFor(() => expect(harnessApi.listModels).toHaveBeenCalledOnce());
    expect(agentsApi.updateBackendSettings).not.toHaveBeenCalled();
  });

  it("loads and persists Qoder-owned models through the same selector", async () => {
    mockUseAgentStore.mockReturnValue({
      selectedAgent: "qoder-agent",
      agents: [
        {
          id: "qoder-agent",
          backend: "qoder",
          backend_model: null,
        },
      ],
      updateAgent: mockUpdateAgent,
    });
    vi.mocked(harnessApi.listModels).mockResolvedValue({
      models: [
        {
          id: "auto",
          name: "Auto",
          description: "",
          is_default: true,
          reasoning_efforts: ["high"],
          default_reasoning_effort: "high",
        },
      ],
    });

    renderWithProviders(<HarnessModelSelector providerId="qoder" />);

    await waitFor(() =>
      expect(harnessApi.listModels).toHaveBeenCalledWith("qoder"),
    );
    await waitFor(() =>
      expect(agentsApi.updateBackendSettings).toHaveBeenCalledWith(
        "qoder-agent",
        {
          model: "auto",
          reasoning_effort: "high",
        },
      ),
    );
  });
});
