import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { useAgentStore } from "../stores/agentStore";
import { useAgentSessionActivityStore } from "../stores/agentSessionActivityStore";
import { useSessionAttentionStore } from "../stores/sessionAttentionStore";
import { useSessionListStore } from "../stores/sessionListStore";
import {
  computeAgentActivity,
  type AgentActivity,
} from "../utils/agentSessionActivity";
import { getSessionIdFromPath } from "../utils/sessionRoute";

/**
 * Activity of every known agent for its list indicator. The selected agent
 * is derived from the sidebar session list (3 s refresh + local SSE state);
 * other agents come from the on-demand activity store.
 */
export function useAgentActivityMap(): Record<
  string,
  AgentActivity | undefined
> {
  const selectedAgent = useAgentStore((state) => state.selectedAgent);
  const selectedSessions = useSessionListStore((state) => state.sessions);
  const sessionsByAgent = useAgentSessionActivityStore(
    (state) => state.sessionsByAgent,
  );
  const seenFinishedAt = useSessionAttentionStore(
    (state) => state.seenFinishedAt,
  );
  const location = useLocation();
  const currentSessionId = getSessionIdFromPath(location.pathname);

  return useMemo(() => {
    const visible = document.visibilityState === "visible";
    const activity: Record<string, AgentActivity | undefined> = {};
    for (const [agentId, sessions] of Object.entries(sessionsByAgent)) {
      if (agentId === selectedAgent) continue;
      activity[agentId] = computeAgentActivity({
        agentId,
        sessions,
        seenFinishedAt,
        visible,
      });
    }
    activity[selectedAgent] = computeAgentActivity({
      agentId: selectedAgent,
      sessions: selectedSessions,
      seenFinishedAt,
      currentSessionId,
      visible,
    });
    return activity;
  }, [
    currentSessionId,
    seenFinishedAt,
    selectedAgent,
    selectedSessions,
    sessionsByAgent,
  ]);
}
