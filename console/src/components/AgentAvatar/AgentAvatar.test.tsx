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
});
