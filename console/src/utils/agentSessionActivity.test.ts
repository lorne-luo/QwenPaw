import { describe, expect, it } from "vitest";
import {
  computeAgentActivity,
  countsForAgentIndicator,
  type ActivitySession,
} from "./agentSessionActivity";

const FINISHED = "2026-10-03T08:00:00.000Z";

const finished = (id: string, extra: Partial<ActivitySession> = {}) => ({
  id,
  status: "idle",
  lastFinishedAt: FINISHED,
  ...extra,
});

function compute(
  sessions: ActivitySession[],
  seenFinishedAt: Record<string, string | null>,
  options: { agentId?: string; currentSessionId?: string; visible?: boolean },
) {
  return computeAgentActivity({
    agentId: options.agentId ?? "agent-a",
    sessions,
    seenFinishedAt,
    currentSessionId: options.currentSessionId,
    visible: options.visible ?? true,
  });
}

describe("countsForAgentIndicator", () => {
  it.each([
    [{ id: "c" }, true],
    [{ id: "c", source: "chat" }, true],
    [{ id: "c", source: "cron" }, false],
    [{ id: "c", source: "subagent" }, false],
    [{ id: "c", sessionId: "qa-agent:to:agent-a:1:abc" }, false],
    [{ id: "c", sessionId: "console:user" }, true],
    [{ id: "c", counted: true }, true],
    [{ id: "c", counted: false }, false],
  ] as const)("%o counts: %s", (session, expected) => {
    expect(countsForAgentIndicator(session)).toBe(expected);
  });
});

describe("computeAgentActivity", () => {
  it("is idle without running or unseen sessions", () => {
    expect(
      compute([finished("c1")], { "agent-a:c1": FINISHED }, {}),
    ).toBeUndefined();
  });

  it("prefers generating over an unseen result", () => {
    const sessions = [
      finished("c1"),
      { id: "c2", status: "running", lastFinishedAt: null },
    ];
    expect(compute(sessions, { "agent-a:c1": null }, {})).toBe("generating");
  });

  it("treats a local generating flag as generating", () => {
    expect(compute([{ id: "c1", generating: true }], {}, {})).toBe(
      "generating",
    );
  });

  it("reports an unseen completed reply", () => {
    expect(compute([finished("c1")], { "agent-a:c1": null }, {})).toBe(
      "unseen",
    );
  });

  it("ignores cron, subagent and inter-agent sessions", () => {
    const sessions = [
      { id: "c1", status: "running", source: "cron" },
      { id: "c2", generating: true, source: "subagent" },
      finished("c3", { sessionId: "qa:to:agent-a:1:abc" }),
    ];
    expect(compute(sessions, { "agent-a:c3": null }, {})).toBeUndefined();
  });

  it("ignores sessions the backend did not count", () => {
    const sessions = [
      { id: "c1", status: "running", counted: false },
      finished("c2", { counted: false }),
    ];
    expect(compute(sessions, { "agent-a:c2": null }, {})).toBeUndefined();
  });

  it("excludes the visible current session by id or realId", () => {
    const byId = finished("c1");
    const byRealId = finished("1785114733908-0l0jmai", { realId: "c2" });
    const seen = { "agent-a:c1": null, "agent-a:c2": null };

    expect(compute([byId], seen, { currentSessionId: "c1" })).toBeUndefined();
    expect(
      compute([byRealId], seen, { currentSessionId: "c2" }),
    ).toBeUndefined();
    expect(
      compute([byRealId], seen, { currentSessionId: "1785114733908-0l0jmai" }),
    ).toBeUndefined();
    expect(compute([byId, byRealId], seen, { currentSessionId: "c1" })).toBe(
      "unseen",
    );
  });

  it("counts the current session while the tab is hidden", () => {
    expect(
      compute(
        [finished("c1")],
        { "agent-a:c1": null },
        { currentSessionId: "c1", visible: false },
      ),
    ).toBe("unseen");
  });

  it("keeps the same chat id isolated per agent", () => {
    const seen = { "agent-a:c1": null, "agent-b:c1": FINISHED };
    expect(compute([finished("c1")], seen, { agentId: "agent-a" })).toBe(
      "unseen",
    );
    expect(
      compute([finished("c1")], seen, { agentId: "agent-b" }),
    ).toBeUndefined();
  });
});
