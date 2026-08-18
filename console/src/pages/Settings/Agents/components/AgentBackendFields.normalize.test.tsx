import { fireEvent, screen } from "@testing-library/react";
import { Form } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/common_setup";
import { AgentBackendFields } from "./AgentBackendFields";

const { mockMessage, mockProvider } = vi.hoisted(() => ({
  mockMessage: { success: vi.fn(), error: vi.fn() },
  mockProvider: {
    id: "claude",
    name: "Claude Code",
    available: true,
    coming_soon: false,
    installed: true,
    authenticated: true,
    account: { type: "oauth_token" },
    runtime_path: "/opt/claude",
    runtime_source: "python-sdk",
    error: null,
    capabilities: {
      authentication: true,
      model_selection: true,
      reasoning_effort: true,
      reasoning_stream: true,
      tool_stream: true,
      session_resume: true,
      workspace_ui: false,
      native_skills_ui: false,
      native_tools_ui: false,
      native_mcp_ui: false,
      qwenpaw_skills_projection: true,
      qwenpaw_mcp_projection: true,
      provider_skills_discovery: true,
      provider_mcp_discovery: false,
      mcp_tool_allowlist: true,
      passthrough_commands: false,
      loop_modes: false,
      attachments: true,
      context_usage: false,
      skills_commands: false,
      commands: [],
      approval_presets: [],
    },
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/api/modules/harness", () => ({
  harnessApi: {
    status: vi.fn().mockResolvedValue(mockProvider),
    list: vi.fn().mockResolvedValue({ providers: [] }),
    listModels: vi.fn().mockResolvedValue({ models: [] }),
    login: vi.fn(),
    logout: vi.fn(),
  },
}));

vi.mock("@/utils/clipboard", () => ({ copyText: vi.fn() }));

vi.mock("@/hooks/useAppMessage", () => ({
  useAppMessage: () => ({ message: mockMessage }),
}));

import { harnessApi } from "@/api/modules/harness";

function ClaudeForm({ env }: { env: unknown }) {
  const [form] = Form.useForm();
  return (
    <Form
      form={form}
      initialValues={{ backend: "claude", backend_settings: { env } }}
    >
      <AgentBackendFields form={form} open />
    </Form>
  );
}

describe("AgentBackendFields environment normalization", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(harnessApi.status).mockResolvedValue(mockProvider);
  });

  it("converts a hand-edited env object into editable rows", async () => {
    renderWithProviders(
      <ClaudeForm env={{ ANTHROPIC_BASE_URL: "https://gateway" }} />,
    );

    const keys = await screen.findAllByPlaceholderText(
      "agent.backend.claudeEnvKeyPlaceholder",
    );
    const values = screen.getAllByPlaceholderText(
      "agent.backend.claudeEnvValuePlaceholder",
    );

    expect(keys).toHaveLength(1);
    expect(keys[0]).toHaveValue("ANTHROPIC_BASE_URL");
    expect(values[0]).toHaveValue("https://gateway");
  });

  it("keeps existing rows untouched and probes with them", async () => {
    renderWithProviders(<ClaudeForm env={[{ name: "A", value: "1" }]} />);

    const keys = await screen.findAllByPlaceholderText(
      "agent.backend.claudeEnvKeyPlaceholder",
    );
    expect(keys).toHaveLength(1);
    expect(keys[0]).toHaveValue("A");

    fireEvent.click(screen.getByText("agent.backend.detect"));

    expect(harnessApi.status).toHaveBeenLastCalledWith("claude", {
      env: [{ name: "A", value: "1" }],
    });
  });
});
