import {
  hasUnseenCompletion,
  type AttentionSession,
} from "../stores/sessionAttentionStore";

export type AgentActivity = "generating" | "unseen";

export interface ActivitySession extends AttentionSession {
  status?: string;
  generating?: boolean;
  source?: string | null;
  sessionId?: string;
  /** Backend verdict for activity-store sessions (absent: decide locally). */
  counted?: boolean;
}

export function matchesSessionId(
  session: AttentionSession,
  sessionId: string,
): boolean {
  return session.id === sessionId || session.realId === sessionId;
}

/** Cron, subagent and inter-agent (":to:") sessions never color an agent. */
export function countsForAgentIndicator(session: ActivitySession): boolean {
  return (
    session.counted !== false &&
    (session.source ?? "chat") === "chat" &&
    !(session.sessionId ?? "").includes(":to:")
  );
}

interface ComputeAgentActivityInput {
  agentId: string;
  sessions: ActivitySession[];
  seenFinishedAt: Record<string, string | null>;
  /** The session shown in Chat; only set for the selected agent. */
  currentSessionId?: string;
  visible: boolean;
}

export function computeAgentActivity({
  agentId,
  sessions,
  seenFinishedAt,
  currentSessionId,
  visible,
}: ComputeAgentActivityInput): AgentActivity | undefined {
  const counted = sessions.filter(countsForAgentIndicator);
  if (
    counted.some(
      (session) => session.status === "running" || session.generating === true,
    )
  ) {
    return "generating";
  }
  const hasUnseen = counted.some(
    (session) =>
      !(
        visible &&
        !!currentSessionId &&
        matchesSessionId(session, currentSessionId)
      ) && hasUnseenCompletion(seenFinishedAt, agentId, session),
  );
  return hasUnseen ? "unseen" : undefined;
}
