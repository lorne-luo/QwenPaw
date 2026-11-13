import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  AGENT_ICON_COLORS,
  AGENT_ICONS,
  DEFAULT_ICON_COLOR,
  formatLucideIcon,
  parseLucideIcon,
} from "./presets";

describe("agent icon presets", () => {
  it("round-trips lucide values and ignores emoji", () => {
    expect(parseLucideIcon(formatLucideIcon("ship-wheel", "blue"))).toEqual({
      name: "ship-wheel",
      color: "blue",
    });
    expect(parseLucideIcon("🦊")).toBeNull();
    expect(parseLucideIcon(undefined)).toBeNull();
  });

  // The backend draws /api/agents/{id}/avatar.svg from this exported copy.
  it("matches the backend icon set (run scripts/export-agent-icons.mjs)", () => {
    const exported = JSON.parse(
      readFileSync(
        resolve(process.cwd(), "../src/qwenpaw/app/agent_avatar_icons.json"),
        "utf8",
      ),
    );
    expect(exported.defaultColor).toBe(DEFAULT_ICON_COLOR);
    expect(exported.colors).toEqual(AGENT_ICON_COLORS);
    expect(Object.keys(exported.icons).sort()).toEqual(
      Object.keys(AGENT_ICONS).sort(),
    );
  });
});
