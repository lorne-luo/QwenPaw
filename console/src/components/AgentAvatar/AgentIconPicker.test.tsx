import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentIconPicker } from "./AgentIconPicker";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("AgentIconPicker", () => {
  it("emits the picked icon in the default color", () => {
    const onChange = vi.fn();
    render(<AgentIconPicker value="" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "telescope" }));
    expect(onChange).toHaveBeenCalledWith("lucide:telescope:blue");
  });

  it("recolors the selected icon when a swatch is picked", () => {
    const onChange = vi.fn();
    render(
      <AgentIconPicker value="lucide:telescope:blue" onChange={onChange} />,
    );
    expect(screen.getByRole("button", { name: "telescope" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("radio", { name: "pink" }));
    expect(onChange).toHaveBeenCalledWith("lucide:telescope:pink");
  });

  it("uses a remembered color for the next pick when nothing is selected", () => {
    const onChange = vi.fn();
    render(<AgentIconPicker value="🦊" onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: "teal" }));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "rocket" }));
    expect(onChange).toHaveBeenCalledWith("lucide:rocket:teal");
  });

  it("follows the color of a new value from the form", () => {
    const { rerender } = render(
      <AgentIconPicker value="lucide:ship:blue" onChange={vi.fn()} />,
    );
    rerender(<AgentIconPicker value="lucide:ship:red" onChange={vi.fn()} />);
    expect(screen.getByRole("radio", { name: "red" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("clears the value with the default option", () => {
    const onChange = vi.fn();
    render(<AgentIconPicker value="lucide:ship:blue" onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "agent.iconDefault" }));
    expect(onChange).toHaveBeenCalledWith("");
  });
});
