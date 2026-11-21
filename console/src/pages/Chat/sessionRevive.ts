export type PrepareSessionReviveOptions<T = unknown> = {
  sessionId: string;
  invalidate: (sessionId: string) => void;
  preload: (sessionId: string) => Promise<T>;
  isCurrent: () => boolean;
  /** True when the fresh data matches what is on screen: skip the remount. */
  isUnchanged?: (fresh: T) => boolean;
  remount: (fresh: T) => void;
};

/**
 * Fetch a fresh session while the current chat remains visible, then swap the
 * runtime only when the prefetched result can be consumed immediately and
 * differs from what is on screen. Resolves `true` only when it remounted.
 */
export async function prepareSessionRevive<T>({
  sessionId,
  invalidate,
  preload,
  isCurrent,
  isUnchanged,
  remount,
}: PrepareSessionReviveOptions<T>): Promise<boolean> {
  invalidate(sessionId);
  let fresh: T;
  try {
    fresh = await preload(sessionId);
  } catch {
    return false;
  }
  if (!isCurrent()) return false;
  if (isUnchanged?.(fresh)) return false;
  remount(fresh);
  return true;
}

type HistoryPart = { metadata?: { original_id?: unknown } | null };

type ConversationEntry = {
  role?: string;
  cards?: readonly { data?: unknown }[];
};

/**
 * Persisted identity of one message, or null when it has none. The history
 * API regenerates every message id on each read, so identity comes from
 * `metadata.original_id` (the stored memory id): the user message itself, or
 * the reply's output count plus its last output — a reply grows in place when
 * a background run appends to it.
 */
function messageSignature(message: ConversationEntry): string | null {
  const data = message.cards?.[0]?.data as
    | { input?: HistoryPart[]; output?: HistoryPart[] }
    | undefined;
  const parts = message.role === "user" ? data?.input : data?.output;
  const id = parts?.[parts.length - 1]?.metadata?.original_id;
  if (!parts || typeof id !== "string" || !id) return null;
  return `${message.role}:${parts.length}:${id}`;
}

/**
 * Whether two message lists describe the same persisted conversation. A
 * message without a persisted identity (e.g. one sent live in this tab) never
 * matches, so any doubt resolves to a remount.
 */
export function isSameConversation(
  shown: readonly ConversationEntry[],
  fresh: readonly ConversationEntry[],
): boolean {
  if (shown.length !== fresh.length) return false;
  return shown.every((message, index) => {
    const signature = messageSignature(message);
    return signature !== null && signature === messageSignature(fresh[index]);
  });
}

/**
 * Whether a revive can keep the current runtime: nothing is streaming here,
 * the backend reports no run in progress, and the fresh history is the one
 * on screen.
 */
export function isReviveUnchanged({
  streaming,
  shown,
  fresh,
}: {
  streaming: boolean;
  shown: readonly ConversationEntry[];
  fresh: {
    generating?: boolean;
    messages?: readonly ConversationEntry[];
  };
}): boolean {
  return (
    !streaming &&
    !fresh.generating &&
    isSameConversation(shown, fresh.messages ?? [])
  );
}

/**
 * Whether a remounted runtime shows enough to replace the held copy: some
 * history, and no reply still waiting for its replayed events — that reply
 * would paint as an empty bubble for a moment before its content arrives.
 */
export function isRevivedViewReady(
  messages: readonly ConversationEntry[],
  generating: boolean,
): boolean {
  const last = messages[messages.length - 1];
  if (!last) return false;
  if (last.role === "user") return !generating;
  const data = last.cards?.[0]?.data as
    | { status?: string; output?: readonly unknown[] }
    | undefined;
  const pending = data?.status === "created" || data?.status === "in_progress";
  return !pending || Boolean(data?.output?.length);
}
