import { create } from "zustand";
import { persist } from "zustand/middleware";

const STORAGE_KEY = "qwenpaw-session-attention";

export interface AttentionSession {
  id: string;
  realId?: string;
  lastFinishedAt?: string | null;
}

interface SessionAttentionState {
  seenFinishedAt: Record<string, string | null>;
  /** Agents whose existing completions were already taken as the baseline. */
  baselinedAgents: Record<string, true>;
  initializeSessions: (agentId: string, sessions: AttentionSession[]) => void;
  initializeAgentSessions: (
    sessionsByAgent: Record<string, AttentionSession[]>,
  ) => void;
  markSeen: (agentId: string, session: AttentionSession) => void;
}

type AttentionMarkers = Pick<
  SessionAttentionState,
  "seenFinishedAt" | "baselinedAgents"
>;

export function sessionAttentionKey(
  agentId: string,
  session: AttentionSession,
): string {
  return `${agentId}:${session.realId || session.id}`;
}

export function hasUnseenCompletion(
  seenFinishedAt: Record<string, string | null>,
  agentId: string,
  session: AttentionSession,
): boolean {
  if (!session.lastFinishedAt) return false;
  const key = sessionAttentionKey(agentId, session);
  if (!Object.prototype.hasOwnProperty.call(seenFinishedAt, key)) return false;
  const seenAt = seenFinishedAt[key];
  if (!seenAt) return true;
  const finishedMs = Date.parse(session.lastFinishedAt);
  const seenMs = Date.parse(seenAt);
  return (
    Number.isFinite(finishedMs) &&
    Number.isFinite(seenMs) &&
    finishedMs > seenMs
  );
}

/**
 * Adds read markers for sessions observed for the first time. Returns the
 * input object unchanged when nothing new was observed.
 */
function applyObservation(
  markers: AttentionMarkers,
  agentId: string,
  sessions: AttentionSession[],
): AttentionMarkers {
  const baselined = Object.prototype.hasOwnProperty.call(
    markers.baselinedAgents,
    agentId,
  );
  let seenFinishedAt = markers.seenFinishedAt;
  for (const session of sessions) {
    const key = sessionAttentionKey(agentId, session);
    if (Object.prototype.hasOwnProperty.call(seenFinishedAt, key)) continue;
    if (seenFinishedAt === markers.seenFinishedAt) {
      seenFinishedAt = { ...seenFinishedAt };
    }
    // The first observation of an agent takes existing completions as the
    // baseline, so an upgrade does not mark all history unread. Afterwards a
    // session that shows up already finished is a new, unseen result.
    seenFinishedAt[key] = baselined ? null : session.lastFinishedAt ?? null;
  }
  if (baselined && seenFinishedAt === markers.seenFinishedAt) return markers;
  return {
    seenFinishedAt,
    baselinedAgents: baselined
      ? markers.baselinedAgents
      : { ...markers.baselinedAgents, [agentId]: true },
  };
}

export const useSessionAttentionStore = create<SessionAttentionState>()(
  persist(
    (set) => ({
      seenFinishedAt: {},
      baselinedAgents: {},

      initializeSessions: (agentId, sessions) =>
        set((state) => applyObservation(state, agentId, sessions)),

      initializeAgentSessions: (sessionsByAgent) =>
        set((state) => {
          let next: AttentionMarkers = state;
          for (const [agentId, sessions] of Object.entries(sessionsByAgent)) {
            next = applyObservation(next, agentId, sessions);
          }
          return next;
        }),

      markSeen: (agentId, session) => {
        if (!session.lastFinishedAt) return;
        const key = sessionAttentionKey(agentId, session);
        set((state) => {
          if (state.seenFinishedAt[key] === session.lastFinishedAt)
            return state;
          return {
            seenFinishedAt: {
              ...state.seenFinishedAt,
              [key]: session.lastFinishedAt ?? null,
            },
          };
        });
      },
    }),
    {
      name: STORAGE_KEY,
      partialize: (state) => ({
        seenFinishedAt: state.seenFinishedAt,
        baselinedAgents: state.baselinedAgents,
      }),
    },
  ),
);
