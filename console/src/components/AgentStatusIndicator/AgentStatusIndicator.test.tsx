import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentStatusIndicator } from "./index";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("AgentStatusIndicator", () => {
  it.each([
    ["disabled", "agent.status.disabled"],
    ["pending", "agent.status.pending"],
    ["starting", "agent.status.starting"],
    ["running", "agent.status.running"],
    ["failed", "agent.status.failed"],
  ] as const)("renders %s status", (status, label) => {
    render(<AgentStatusIndicator status={status} />);
    const indicator = screen.getByRole("status", { name: label });
    expect(indicator).toHaveAttribute("data-status", status);
    expect(indicator).toHaveAttribute("data-activity", "none");
  });

  it("keeps an idle running agent's slot and label without a colored dot", () => {
    render(<AgentStatusIndicator status="running" />);
    const indicator = screen.getByRole("status", {
      name: "agent.status.running",
    });
    expect(indicator.className).toContain("runningIdle");
  });

  it.each([
    ["generating", "chat.statusInProgress"],
    ["unseen", "chat.statusUnseenResult"],
  ] as const)("shows %s activity for a running agent", (activity, label) => {
    render(<AgentStatusIndicator status="running" activity={activity} />);
    const indicator = screen.getByRole("status", { name: label });
    expect(indicator).toHaveAttribute("data-status", "running");
    expect(indicator).toHaveAttribute("data-activity", activity);
    expect(indicator.className).toContain(activity);
  });

  it.each(
    (["disabled", "pending", "starting", "failed"] as const).flatMap((status) =>
      (["generating", "unseen"] as const).map(
        (activity) => [status, activity] as const,
      ),
    ),
  )("ignores %s + %s activity", (status, activity) => {
    render(<AgentStatusIndicator status={status} activity={activity} />);
    const indicator = screen.getByRole("status", {
      name: `agent.status.${status}`,
    });
    expect(indicator).toHaveAttribute("data-status", status);
    expect(indicator).toHaveAttribute("data-activity", "none");
    expect(indicator.className).toContain(status);
    expect(indicator.className).not.toContain(activity);
  });

  it("ignores activity for a disabled agent without a startup status", () => {
    render(<AgentStatusIndicator enabled={false} activity="generating" />);
    expect(
      screen.getByRole("status", { name: "agent.status.disabled" }),
    ).toHaveAttribute("data-activity", "none");
  });

  it("gives disabled state precedence over a stale runtime status", () => {
    render(<AgentStatusIndicator status="running" enabled={false} />);

    expect(
      screen.getByRole("status", { name: "agent.status.disabled" }),
    ).toHaveAttribute("data-status", "disabled");
  });
});
