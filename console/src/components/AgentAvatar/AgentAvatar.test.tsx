import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentAvatar } from "./index";

describe("AgentAvatar", () => {
  it("renders the emoji as a sized text node", () => {
    const { container } = render(<AgentAvatar icon="🦊" size={18} />);
    const node = container.firstElementChild as HTMLElement;
    expect(node.tagName).toBe("SPAN");
    expect(node).toHaveTextContent("🦊");
    expect(node).toHaveAttribute("aria-hidden", "true");
    expect(node.style.width).toBe("18px");
    expect(node.style.height).toBe("18px");
    expect(container.querySelector("svg")).toBeNull();
  });

  it.each([undefined, "", "   "])(
    "falls back to the bot icon for %j",
    (icon) => {
      const { container } = render(
        <AgentAvatar icon={icon} size={14} strokeWidth={2} />,
      );
      const svg = container.querySelector("svg");
      expect(svg).not.toBeNull();
      expect(svg).toHaveAttribute("width", "14");
      expect(container.querySelector("span")).toBeNull();
    },
  );

  it("renders a lucide value as a gradient badge with a white glyph", () => {
    const { container } = render(
      <AgentAvatar icon="lucide:telescope:indigo" size={20} />,
    );
    const badge = container.firstElementChild as HTMLElement;
    expect(badge.tagName).toBe("SPAN");
    expect(badge.style.width).toBe("20px");
    expect(badge.style.background).toContain("rgb(129, 140, 248)");
    const svg = badge.querySelector("svg");
    expect(svg).toHaveClass("lucide-telescope");
    expect(svg).toHaveAttribute("width", "13");
  });

  it("falls back to the bot glyph and default color for unknown parts", () => {
    const { container } = render(
      <AgentAvatar icon="lucide:no-such-icon:no-such-color" size={20} />,
    );
    const badge = container.firstElementChild as HTMLElement;
    expect(badge.style.background).toContain("rgb(96, 165, 250)");
    expect(badge.querySelector("svg")).toHaveClass("lucide-bot");
  });
});
