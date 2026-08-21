/**
 * The sidebar's quick-create "+" rides antd's trailing `extra` slot, so the
 * Menu itself handles right-alignment. That contract is worth pinning: the
 * slot only exists on leaf items, and a click inside it must not also
 * navigate the row.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Menu } from "antd";
import { toAntdItems } from "./adapter";
import type { MenuItem } from "../../plugins/registry/types";

const menu: MenuItem[] = [
  { id: "core.mcp", location: "primary.agentScoped", label: "MCP" },
  { id: "core.tools", location: "primary.agentScoped", label: "Tools" },
];

function renderMenu(
  collapsed: boolean,
  onRowClick = vi.fn(),
  onPlus = vi.fn(),
) {
  const items = toAntdItems(menu, {
    collapsed,
    getExtra: (item) =>
      item.id === "core.mcp" ? (
        <span
          role="button"
          aria-label="add"
          onClick={(event) => {
            event.stopPropagation();
            onPlus();
          }}
        >
          +
        </span>
      ) : null,
  });
  render(<Menu mode="inline" items={items} onClick={onRowClick} />);
  return { onRowClick, onPlus };
}

describe("toAntdItems getExtra", () => {
  it("renders the slot only for items that opt in", () => {
    renderMenu(false);
    expect(screen.getByRole("button", { name: "add" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "add" })).toHaveLength(1);
  });

  it("omits the slot when collapsed — the rail has no room", () => {
    renderMenu(true);
    expect(screen.queryByRole("button", { name: "add" })).toBeNull();
  });

  it("clicking the slot does not also activate the row", async () => {
    const { onRowClick, onPlus } = renderMenu(false);

    await userEvent.click(screen.getByRole("button", { name: "add" }));

    expect(onPlus).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
  });
});
