import type { ChatSpec } from "../../../../api/types";

export interface Session extends ChatSpec {
  name?: string;
}

/**
 * Normalize ISO timestamp to ensure UTC timezone is always recognized.
 * Timestamps without timezone suffix (e.g. from datetime.utcnow()) are
 * treated as local time by browsers, causing incorrect display. Appending
 * 'Z' forces UTC interpretation, consistent with timezone-aware timestamps.
 */
const normalizeTimestamp = (timestamp: string): string => {
  if (/[Z+-]\d{2}:?\d{2}$/.test(timestamp) || timestamp.endsWith("Z")) {
    return timestamp;
  }
  return timestamp + "Z";
};

/**
 * Epoch ms of a session's last activity: updated_at, falling back to
 * created_at. Returns 0 when neither is usable so untimed rows sort last.
 */
export const getLastActivityTime = (
  session: Pick<Session, "created_at" | "updated_at">,
): number => {
  const raw = session.updated_at ?? session.created_at;
  if (!raw) return 0;
  const time = new Date(normalizeTimestamp(raw)).getTime();
  return Number.isNaN(time) ? 0 : time;
};

export const formatTime = (timestamp: string | number | null): string => {
  if (timestamp === null || timestamp === undefined) return "N/A";
  const normalized =
    typeof timestamp === "string" ? normalizeTimestamp(timestamp) : timestamp;
  const date = new Date(normalized);
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};
