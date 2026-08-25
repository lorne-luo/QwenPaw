export type PrepareSessionReviveOptions = {
  sessionId: string;
  invalidate: (sessionId: string) => void;
  preload: (sessionId: string) => Promise<unknown>;
  isCurrent: () => boolean;
  remount: () => void;
};

/**
 * Fetch a fresh session while the current chat remains visible, then swap the
 * runtime only when the prefetched result can be consumed immediately.
 */
export async function prepareSessionRevive({
  sessionId,
  invalidate,
  preload,
  isCurrent,
  remount,
}: PrepareSessionReviveOptions): Promise<boolean> {
  invalidate(sessionId);
  try {
    await preload(sessionId);
  } catch {
    return false;
  }
  if (!isCurrent()) return false;
  remount();
  return true;
}
