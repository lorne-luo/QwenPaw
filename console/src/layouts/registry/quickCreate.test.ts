import { describe, it, expect, beforeEach, vi } from "vitest";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";
import { BUILTIN_MENU } from "./builtinMenu";
import { BUILTIN_ROUTES } from "./builtinRoutes";
import {
  QUICK_CREATE_EVENT,
  QUICK_CREATE_PENDING_KEY,
  QUICK_CREATE_SPECS,
  requestQuickCreate,
} from "./quickCreate";

describe("requestQuickCreate", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("dispatches directly when already on the target page", () => {
    const navigate = vi.fn();
    const listener = vi.fn();
    window.addEventListener(QUICK_CREATE_EVENT, listener);

    requestQuickCreate("core.mcp", "/mcp", "/mcp", navigate);

    expect(navigate).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(QUICK_CREATE_PENDING_KEY)).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    const event = listener.mock.calls[0][0] as CustomEvent<{ menuId: string }>;
    expect(event.detail.menuId).toBe("core.mcp");

    window.removeEventListener(QUICK_CREATE_EVENT, listener);
  });

  it("stashes the menu id and navigates when on another page", () => {
    const navigate = vi.fn();
    const listener = vi.fn();
    window.addEventListener(QUICK_CREATE_EVENT, listener);

    requestQuickCreate("core.mcp", "/mcp", "/chat", navigate);

    expect(navigate).toHaveBeenCalledWith("/mcp");
    expect(sessionStorage.getItem(QUICK_CREATE_PENDING_KEY)).toBe("core.mcp");
    expect(listener).not.toHaveBeenCalled();

    window.removeEventListener(QUICK_CREATE_EVENT, listener);
  });
});

describe("QUICK_CREATE_SPECS", () => {
  const specs = Object.entries(QUICK_CREATE_SPECS);

  it("targets a real menu item that resolves to a route", () => {
    // A spec keyed on a stale/renamed menu id would silently render no "+".
    for (const [menuId] of specs) {
      const item = BUILTIN_MENU.find((entry) => entry.id === menuId);
      expect(item, menuId).toBeDefined();
      const route = BUILTIN_ROUTES.find((r) => r.id === item?.route);
      expect(route, menuId).toBeDefined();
    }
  });

  it("has a translated tooltip in en and zh", () => {
    const lookup = (bundle: Record<string, unknown>, key: string) =>
      key
        .split(".")
        .reduce<unknown>(
          (node, part) => (node as Record<string, unknown> | undefined)?.[part],
          bundle,
        );

    for (const [menuId, spec] of specs) {
      expect(lookup(en, spec.tooltipKey), `${menuId} (en)`).toBeTypeOf(
        "string",
      );
      expect(lookup(zh, spec.tooltipKey), `${menuId} (zh)`).toBeTypeOf(
        "string",
      );
      expect(spec.tooltipFallback.length).toBeGreaterThan(0);
    }
  });
});
