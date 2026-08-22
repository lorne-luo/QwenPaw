import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { useSessionAttention } from "@/hooks/useSessionAttention";
import { useAgentStore } from "@/stores/agentStore";
import { useSessionListStore } from "@/stores/sessionListStore";
import { getSessionIdFromPath } from "@/utils/sessionRoute";

const LOCAL_SESSION_ID = /^\d+-[a-z0-9]+$/;

/**
 * Marks the selected agent's visible session seen even when the sidebar
 * session list is not mounted (collapsed sidebar, mobile).
 */
export function SessionAttentionController() {
  const selectedAgent = useAgentStore((state) => state.selectedAgent);
  const storeSessions = useSessionListStore((state) => state.sessions);
  const location = useLocation();
  const currentSessionId = getSessionIdFromPath(location.pathname);

  // Observe the same sessions as the sidebar list: archived chats and
  // not-yet-resolved local chats are not part of it.
  const sessions = useMemo(
    () =>
      storeSessions.filter(
        (session) =>
          !session.archived &&
          (!!session.realId || !LOCAL_SESSION_ID.test(session.id)),
      ),
    [storeSessions],
  );

  useSessionAttention(selectedAgent, sessions, currentSessionId);
  return null;
}
