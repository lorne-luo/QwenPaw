/**
 * Sidebar "+" quick-create delivery, both channels:
 *
 *   - live window event, when the target page is already mounted
 *   - sessionStorage handoff, when the sidebar navigated to the page first
 *     (the page is lazily loaded, so it cannot hear an event dispatched
 *     before it mounts)
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useQuickCreate } from "./useQuickCreate";
import {
  QUICK_CREATE_EVENT,
  QUICK_CREATE_PENDING_KEY,
} from "../layouts/registry/quickCreate";

function dispatch(menuId: string) {
  window.dispatchEvent(
    new CustomEvent(QUICK_CREATE_EVENT, { detail: { menuId } }),
  );
}

describe("useQuickCreate", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("triggers on a matching event", () => {
    const onTrigger = vi.fn();
    renderHook(() => useQuickCreate("core.mcp", onTrigger));

    act(() => dispatch("core.mcp"));

    expect(onTrigger).toHaveBeenCalledTimes(1);
  });

  it("ignores events aimed at another menu id", () => {
    const onTrigger = vi.fn();
    renderHook(() => useQuickCreate("core.mcp", onTrigger));

    act(() => dispatch("core.acp"));

    expect(onTrigger).not.toHaveBeenCalled();
  });

  it("consumes a matching pending flag on mount", () => {
    sessionStorage.setItem(QUICK_CREATE_PENDING_KEY, "core.mcp");
    const onTrigger = vi.fn();

    renderHook(() => useQuickCreate("core.mcp", onTrigger));

    expect(onTrigger).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(QUICK_CREATE_PENDING_KEY)).toBeNull();
  });

  it("leaves another page's pending flag untouched", () => {
    sessionStorage.setItem(QUICK_CREATE_PENDING_KEY, "core.acp");
    const onTrigger = vi.fn();

    renderHook(() => useQuickCreate("core.mcp", onTrigger));

    expect(onTrigger).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(QUICK_CREATE_PENDING_KEY)).toBe("core.acp");
  });

  it("calls the latest handler after a re-render", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(
      ({ handler }: { handler: () => void }) =>
        useQuickCreate("core.mcp", handler),
      { initialProps: { handler: first } },
    );

    rerender({ handler: second });
    act(() => dispatch("core.mcp"));

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("stops listening after unmount", () => {
    const onTrigger = vi.fn();
    const { unmount } = renderHook(() => useQuickCreate("core.mcp", onTrigger));

    unmount();
    act(() => dispatch("core.mcp"));

    expect(onTrigger).not.toHaveBeenCalled();
  });
});
