import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/common_setup";
import type { ChatGroup } from "../../api/types/chat";
import SessionGroupHeader, { UnseenGroupHeader } from "./index";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, fallback?: string) => fallback ?? _key,
  }),
}));

const group: ChatGroup = {
  id: "project",
  name: "Project",
  order: 0,
  kind: "custom",
  pinned: false,
};

describe("SessionGroupHeader", () => {
  it("toggles from the header keyboard target and prevents space scrolling", () => {
    const onToggle = vi.fn();
    renderWithProviders(
      <SessionGroupHeader
        group={group}
        count={3}
        collapsed={false}
        onToggle={onToggle}
      />,
    );

    const header = screen.getByRole("button", { name: /Project/ });
    const event = new KeyboardEvent("keydown", {
      key: " ",
      bubbles: true,
      cancelable: true,
    });
    fireEvent(header, event);

    expect(event.defaultPrevented).toBe(true);
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("allows spaces and enter while renaming without toggling the group", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    const onRename = vi.fn();
    renderWithProviders(
      <SessionGroupHeader
        group={group}
        count={3}
        collapsed={false}
        onToggle={onToggle}
        onRename={onRename}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Manage group" }));
    await user.click(await screen.findByText("Rename"));

    const input = screen.getByRole("textbox");
    await user.type(input, " Alpha");
    expect(input).toHaveValue("Project Alpha");
    expect(onToggle).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(onRename).toHaveBeenCalledWith("Project Alpha");
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("renders the unread group as a fixed, non-interactive header", () => {
    renderWithProviders(<UnseenGroupHeader count={2} />);

    expect(screen.getByText("Unfinished")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("calls onMarkAllSeen from the unread group header button", () => {
    const onMarkAllSeen = vi.fn();
    renderWithProviders(
      <UnseenGroupHeader count={2} onMarkAllSeen={onMarkAllSeen} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Mark all as read" }));
    expect(onMarkAllSeen).toHaveBeenCalledTimes(1);
  });
});
