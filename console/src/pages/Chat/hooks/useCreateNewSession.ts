import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useChatAnywhereSessionsState } from "@agentscope-ai/chat";
import { CHAT_BASE_PATH } from "../../../utils/sessionRoute";
import { useAgentStore } from "../../../stores/agentStore";
import sessionApi from "../sessionApi";
import { focusChatComposerWhenReady } from "../utils";
import { clearPendingModel } from "../../../features/session-settings/sessionModel";
import { clearPendingThinking } from "../../../features/thinking/sessionThinkingApi";

/**
 * Open the blank composer. The SDK allocates a backend session on first send;
 * opening this page (including after deletion) must not persist an empty chat.
 *
 * Focuses the composer afterwards: a blank chat has nothing to read, so the
 * only thing the user can do next is type. This is the single choke point for
 * "new blank chat", so every entry point (sidebar "+", session list, chat
 * header) gets the behaviour.
 */
export function useCreateNewSession(): () => Promise<void> {
  const navigate = useNavigate();
  const { setCurrentSessionId } = useChatAnywhereSessionsState();
  return useCallback(async () => {
    sessionApi.invalidateSessionCreation();
    sessionApi.finishSessionSwitch();
    sessionApi.lastActiveChatId = null;
    sessionApi.preferredChatId = null;
    const agents = useAgentStore.getState();
    clearPendingModel(agents.selectedAgent, "new");
    clearPendingThinking(agents.selectedAgent, "new");
    agents.removeLastChatId(agents.selectedAgent);
    setCurrentSessionId(undefined);
    navigate(CHAT_BASE_PATH, { replace: true });
    window.dispatchEvent(new Event("session-model-changed"));
    focusChatComposerWhenReady();
  }, [navigate, setCurrentSessionId]);
}
