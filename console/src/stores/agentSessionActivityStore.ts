/**
 * agentSessionActivityStore — on-demand session status of every enabled,
 * already-loaded agent, used to color the agent list indicators.
 *
 * Not persisted and never polled: agent lists call refresh() when they open
 * and render immediately; indicators update in place when the response lands.
 */
import { create } from "zustand";
import { agentsApi } from "../api/modules/agents";
import type { ChatStatus } from "../api/types/chat";
import { getSessionIdFromPath } from "../utils/sessionRoute";
import { stripRouterBasename } from "../utils/navigationMode";
import { matchesSessionId } from "../utils/agentSessionActivity";
import { useAgentStore } from "./agentStore";
import {
  useSessionAttentionStore,
  type AttentionSession,
} from "./sessionAttentionStore";

export interface AgentActivitySession extends AttentionSession {
  status: ChatStatus;
  /** False for sessions that never color the agent; they only seed markers. */
  counted?: boolean;
}

interface AgentSessionActivityState {
  sessionsByAgent: Record<string, AgentActivitySession[]>;
  /**
   * Fire and forget. Overlapping calls share one request; a failure keeps the
   * last known state silently.
   */
  refresh: (currentSessionId?: string) => Promise<void>;
}

let inFlight: Promise<void> | null = null;

function routeSessionId(): string | undefined {
  return getSessionIdFromPath(stripRouterBasename(window.location.pathname));
}

async function load(currentSessionId: string | undefined): Promise<void> {
  let sessionsByAgent: Record<string, AgentActivitySession[]>;
  try {
    const response = await agentsApi.getSessionActivity();
    sessionsByAgent = {};
    for (const agent of response.agents) {
      sessionsByAgent[agent.agent_id] = agent.sessions.map((session) => ({
        id: session.id,
        status: session.status,
        counted: session.counted,
        lastFinishedAt: session.last_finished_at ?? null,
      }));
    }
  } catch (error) {
    console.warn("Failed to load agent session activity:", error);
    return;
  }

  useAgentSessionActivityStore.setState({ sessionsByAgent });
  const attention = useSessionAttentionStore.getState();
  attention.initializeAgentSessions(sessionsByAgent);

  // Viewing a session is what marks it seen, even when the sidebar list is
  // not mounted (collapsed sidebar, mobile).
  if (!currentSessionId || document.visibilityState !== "visible") return;
  const selectedAgent = useAgentStore.getState().selectedAgent;
  const current = sessionsByAgent[selectedAgent]?.find((session) =>
    matchesSessionId(session, currentSessionId),
  );
  if (current) attention.markSeen(selectedAgent, current);
}

export const useAgentSessionActivityStore = create<AgentSessionActivityState>(
  () => ({
    sessionsByAgent: {},
    refresh: (currentSessionId = routeSessionId()) => {
      if (!inFlight) {
        inFlight = load(currentSessionId).finally(() => {
          inFlight = null;
        });
      }
      return inFlight;
    },
  }),
);
