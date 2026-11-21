import { useCallback, useState, type RefObject } from "react";
import { Checkbox } from "antd";
import { useTranslation } from "react-i18next";
import type { IAgentScopeRuntimeWebUIRef } from "@agentscope-ai/chat";
import { chatApi, type ChatRewindResponse } from "../../api/modules/chat";
import { useAppMessage } from "../../hooks/useAppMessage";
import sessionApi from "./sessionApi";
import { setTextareaValue } from "./utils";
import {
  requestText,
  rewindTargetAt,
  rewindTargetOf,
  userIndexOf,
  type RequestCardData,
  type RewindTarget,
} from "./chatRewind";

interface RewindConfirmProps {
  preview: ChatRewindResponse;
  onRestoreFilesChange: (restore: boolean) => void;
}

function RewindConfirm({ preview, onRestoreFilesChange }: RewindConfirmProps) {
  const { t } = useTranslation();
  const [restoreFiles, setRestoreFiles] = useState(true);
  const files = preview.file_paths;
  return (
    <div>
      <p>{t("chat.rewind.content", { count: preview.removed_count })}</p>
      {files.length > 0 ? (
        <>
          <Checkbox
            checked={restoreFiles}
            onChange={(event) => {
              setRestoreFiles(event.target.checked);
              onRestoreFilesChange(event.target.checked);
            }}
          >
            {t("chat.rewind.restoreFiles", { count: files.length })}
          </Checkbox>
          <ul style={{ margin: "8px 0 0", maxHeight: 160, overflow: "auto" }}>
            {files.map((path) => (
              <li key={path}>
                <code>{path}</code>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p style={{ opacity: 0.7, marginBottom: 0 }}>
          {preview.files_available
            ? t("chat.rewind.noFileChanges")
            : t("chat.rewind.noCheckpoint")}
        </p>
      )}
    </div>
  );
}

/** Put the rewound prompt back in the composer, like voice input does. */
function fillComposer(text: string) {
  const textarea = document
    .querySelector('[class*="sender"]')
    ?.querySelector("textarea") as HTMLTextAreaElement | null;
  if (!textarea) return;
  setTextareaValue(textarea, text);
  textarea.focus();
}

interface UseChatRewindOptions {
  chatRef: RefObject<IAgentScopeRuntimeWebUIRef | null>;
  /** Console session id the SDK shows the chat under. */
  sessionId: string;
  backendChatId?: string;
  agentId: string;
  isBusy: () => boolean;
}

/**
 * Click handler for the rewind action under a user prompt: preview, confirm,
 * cut the chat at that prompt (optionally restoring files), reload the
 * messages and refill the composer with the prompt.
 */
export function useChatRewind({
  chatRef,
  sessionId,
  backendChatId,
  agentId,
  isBusy,
}: UseChatRewindOptions) {
  const { t } = useTranslation();
  const { message, modal } = useAppMessage();

  return useCallback(
    async ({ data }: { data: RequestCardData }) => {
      if (isBusy()) {
        message.warning(t("chat.rewind.busy"));
        return;
      }
      if (!backendChatId) return;
      const fail = (error: unknown) =>
        message.error(error instanceof Error ? error.message : String(error));

      let target: RewindTarget | null = rewindTargetOf(data);
      if (!target) {
        // Sent live in this tab: find the same prompt in fresh history.
        const index = userIndexOf(
          chatRef.current?.messages.getMessages() ?? [],
          data,
        );
        const fresh = await sessionApi.refreshSession(sessionId);
        target =
          fresh && index >= 0
            ? rewindTargetAt(fresh.messages, index, requestText(data))
            : null;
        if (!target) {
          message.error(t("chat.rewind.notFound"));
          return;
        }
      }

      let preview: ChatRewindResponse;
      try {
        preview = await chatApi.rewindChat(
          backendChatId,
          { ...target, dry_run: true },
          { agentId },
        );
      } catch (error) {
        fail(error);
        return;
      }

      let restoreFiles = preview.file_paths.length > 0;
      const rewindTarget = target;
      modal.confirm({
        centered: true,
        title: t("chat.rewind.title"),
        content: (
          <RewindConfirm
            preview={preview}
            onRestoreFilesChange={(restore) => {
              restoreFiles = restore;
            }}
          />
        ),
        okText: t("chat.rewind.ok"),
        okButtonProps: { danger: true },
        cancelText: t("common.cancel"),
        autoFocusButton: "cancel",
        onOk: async () => {
          try {
            const result = await chatApi.rewindChat(
              backendChatId,
              { ...rewindTarget, include_files: restoreFiles },
              { agentId },
            );
            const session = await sessionApi.refreshSession(sessionId);
            if (session) {
              chatRef.current?.messages.setSessionMessages(
                sessionId,
                session.messages,
              );
            }
            fillComposer(result.prompt_text);
            message.success(
              result.files_restored
                ? t("chat.rewind.doneWithFiles", {
                    count: result.file_paths.length,
                  })
                : t("chat.rewind.done"),
            );
          } catch (error) {
            fail(error);
          }
        },
      });
    },
    [agentId, backendChatId, chatRef, isBusy, message, modal, sessionId, t],
  );
}
