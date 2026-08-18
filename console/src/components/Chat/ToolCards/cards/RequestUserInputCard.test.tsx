import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/common_setup";
import type { ToolCallContent } from "../shared/types";
import RequestUserInputCard from "./RequestUserInputCard";
import { harnessUserInputApi } from "../../../../api/modules/harnessUserInput";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
  }),
}));

vi.mock("../../../../api/modules/harnessUserInput", () => ({
  harnessUserInputApi: {
    submit: vi.fn(),
  },
}));

const submit = vi.mocked(harnessUserInputApi.submit);

const content = (
  overrides: Partial<ToolCallContent> = {},
): ToolCallContent => ({
  type: "tool_call",
  id: "item-1",
  name: "request_user_input",
  params: {
    provider_id: "codex",
    request_id: "request-1",
    session_id: "chat-1",
    questions: [
      {
        id: "choice",
        header: "Approach",
        question: "Which approach should I use?",
        options: [
          { label: "First", description: "Use the first approach" },
          { label: "Second", description: "Use the second approach" },
        ],
        isOther: true,
        isSecret: false,
      },
    ],
  },
  status: "calling",
  ...overrides,
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("RequestUserInputCard", () => {
  it("submits a selected option to the original Codex request", async () => {
    submit.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderWithProviders(<RequestUserInputCard content={content()} />);

    expect(screen.getByText("Which approach should I use?")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Submit answer/ }),
    ).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /Second/ }));
    await user.click(screen.getByRole("button", { name: "Submit answer" }));

    await waitFor(() => {
      expect(submit).toHaveBeenCalledWith("codex", "request-1", "chat-1", {
        choice: { answers: ["Second"] },
      });
    });
    expect(await screen.findByText("Answer submitted")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Submit answer/ }),
    ).toBeDisabled();
  });

  it("collects free text and a custom Other option for all questions", async () => {
    submit.mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    const multi = content({
      params: {
        ...content().params,
        questions: [
          {
            id: "secret",
            header: "Token",
            question: "Enter the token",
            isSecret: true,
          },
          {
            id: "choice",
            header: "Approach",
            question: "Which approach?",
            options: [{ label: "First", description: "Use first" }],
            isOther: true,
          },
        ],
      },
    });
    renderWithProviders(<RequestUserInputCard content={multi} />);

    await user.type(screen.getByLabelText("Enter the token"), "secret-value");
    await user.click(screen.getByRole("radio", { name: "Other" }));
    await user.type(screen.getByLabelText("Custom answer"), "Third way");
    await user.click(screen.getByRole("button", { name: "Submit answer" }));

    await waitFor(() => {
      expect(submit).toHaveBeenCalledWith("codex", "request-1", "chat-1", {
        secret: { answers: ["secret-value"] },
        choice: { answers: ["Third way"] },
      });
    });
  });

  it("keeps the question visible when the original request expired", async () => {
    submit.mockRejectedValue(new Error("user_input_expired - status 404"));
    const user = userEvent.setup();
    renderWithProviders(<RequestUserInputCard content={content()} />);

    await user.click(screen.getByRole("radio", { name: /First/ }));
    await user.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(
      await screen.findByText(
        "The original turn is no longer waiting. You can send your answer as a new chat message.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Which approach should I use?")).toBeVisible();
  });

  it("renders replayed expiry as a visible non-interactive question", () => {
    renderWithProviders(
      <RequestUserInputCard
        content={content({
          status: "done",
          result: JSON.stringify({ status: "expired", answers: {} }),
        })}
      />,
    );

    expect(screen.getByText("Which approach should I use?")).toBeVisible();
    expect(
      screen.getByText(/original turn is no longer waiting/i),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Submit answer" }),
    ).toBeDisabled();
  });
});
