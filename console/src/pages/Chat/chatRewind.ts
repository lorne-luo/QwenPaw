import { QWENPAW_CLIENT_MESSAGE_ID_KEY } from "../../utils/clientMessageId";
import { extractUserMessageText } from "./utils";

/** The user prompt a rewind targets, by backend or client message id. */
export interface RewindTarget {
  message_id?: string;
  client_message_id?: string;
}

type InputPart = {
  content?: unknown;
  metadata?: {
    original_id?: unknown;
    metadata?: Record<string, unknown> | null;
  } | null;
};

/** The `data` of a user request card. */
export type RequestCardData = { input?: InputPart[] } | undefined;

type ShownMessage = {
  role?: string;
  cards?: readonly { data?: unknown }[];
};

/** Text of a user request card, as the copy action reads it. */
export function requestText(data: RequestCardData): string {
  return (data?.input || []).map(extractUserMessageText).join("\n").trim();
}

/**
 * Persisted identity of the prompt a request card shows. History cards carry
 * `metadata.original_id`; a prompt sent live in this tab has neither id until
 * the history is read again.
 */
export function rewindTargetOf(data: RequestCardData): RewindTarget | null {
  const metadata = data?.input?.[data.input.length - 1]?.metadata;
  if (typeof metadata?.original_id === "string" && metadata.original_id) {
    return { message_id: metadata.original_id };
  }
  const clientId = metadata?.metadata?.[QWENPAW_CLIENT_MESSAGE_ID_KEY];
  if (typeof clientId === "string" && clientId) {
    return { client_message_id: clientId };
  }
  return null;
}

function userCards(messages: readonly ShownMessage[]): RequestCardData[] {
  return messages
    .filter((message) => message.role === "user")
    .map((message) => message.cards?.[0]?.data as RequestCardData);
}

/** Position of a request card among the user messages shown, or -1. */
export function userIndexOf(
  messages: readonly ShownMessage[],
  data: RequestCardData,
): number {
  return userCards(messages).indexOf(data);
}

/**
 * Identity of the `index`-th user message in freshly read history, provided
 * it still shows the same text; anything else means the lists diverged.
 */
export function rewindTargetAt(
  messages: readonly ShownMessage[],
  index: number,
  text: string,
): RewindTarget | null {
  const data = userCards(messages)[index];
  if (!data || requestText(data) !== text) return null;
  return rewindTargetOf(data);
}
