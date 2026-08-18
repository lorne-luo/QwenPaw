import { useMemo, useState } from "react";
import { Alert, Button, Card, Input, Radio, Typography } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { harnessUserInputApi } from "../../../../api/modules/harnessUserInput";
import type { ToolCallContent } from "../shared/types";
import styles from "./RequestUserInputCard.module.less";

const { Text } = Typography;
const OTHER_VALUE = "__qwenpaw_other__";

type QuestionOption = {
  label: string;
  description?: string;
};

type UserInputQuestion = {
  id: string;
  header?: string;
  question: string;
  options?: QuestionOption[];
  isOther?: boolean;
  isSecret?: boolean;
};

type CompletionState = "answered" | "expired" | "cancelled" | null;

export interface RequestUserInputCardProps {
  content: ToolCallContent;
  isStreaming?: boolean;
}

function questionsFrom(value: unknown): UserInputQuestion[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const item = entry as Record<string, unknown>;
    if (typeof item.id !== "string" || typeof item.question !== "string") {
      return [];
    }
    const options = Array.isArray(item.options)
      ? item.options.flatMap((option) => {
          if (!option || typeof option !== "object") return [];
          const candidate = option as Record<string, unknown>;
          if (typeof candidate.label !== "string") return [];
          return [
            {
              label: candidate.label,
              description:
                typeof candidate.description === "string"
                  ? candidate.description
                  : undefined,
            },
          ];
        })
      : undefined;
    return [
      {
        id: item.id,
        header: typeof item.header === "string" ? item.header : undefined,
        question: item.question,
        options,
        isOther: item.isOther === true,
        isSecret: item.isSecret === true,
      },
    ];
  });
}

function completionFrom(result: unknown): CompletionState {
  let value = result;
  if (typeof result === "string") {
    try {
      value = JSON.parse(result);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object") return null;
  const status = (value as Record<string, unknown>).status;
  return status === "answered" || status === "expired" || status === "cancelled"
    ? status
    : null;
}

const RequestUserInputCard = ({ content }: RequestUserInputCardProps) => {
  const { t } = useTranslation();
  const params = content.params;
  const providerId =
    typeof params.provider_id === "string" ? params.provider_id : "codex";
  const requestId =
    typeof params.request_id === "string" ? params.request_id : "";
  const sessionId =
    typeof params.session_id === "string" ? params.session_id : "";
  const questions = useMemo(
    () => questionsFrom(params.questions),
    [params.questions],
  );
  const replayedCompletion = useMemo(
    () => completionFrom(content.result),
    [content.result],
  );
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [otherAnswers, setOtherAnswers] = useState<Record<string, string>>({});
  const [state, setState] = useState<CompletionState>(replayedCompletion);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const effectiveState = state ?? replayedCompletion;
  const inactive =
    effectiveState === "answered" ||
    effectiveState === "expired" ||
    effectiveState === "cancelled";

  const answerFor = (question: UserInputQuestion): string => {
    const selected = answers[question.id] ?? "";
    return selected === OTHER_VALUE
      ? otherAnswers[question.id] ?? ""
      : selected;
  };
  const complete =
    questions.length > 0 &&
    questions.every((question) => answerFor(question).trim());

  const submit = async () => {
    if (!complete || !requestId || !sessionId || inactive) return;
    setSubmitting(true);
    setError("");
    const payload = Object.fromEntries(
      questions.map((question) => [
        question.id,
        { answers: [answerFor(question).trim()] },
      ]),
    );
    try {
      await harnessUserInputApi.submit(
        providerId,
        requestId,
        sessionId,
        payload,
      );
      setState("answered");
    } catch (caught) {
      const detail = caught instanceof Error ? caught.message : String(caught);
      if (
        detail.includes("user_input_expired") ||
        detail.includes("user_input_session_mismatch") ||
        detail.includes("status 404")
      ) {
        setState("expired");
      } else {
        setError(
          t(
            "tool.userInput.submitFailed",
            "Could not submit the answer. Check your connection and try again.",
          ),
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const expiredMessage = t(
    "tool.userInput.expired",
    "The original turn is no longer waiting. You can send your answer as a new chat message.",
  );

  return (
    <Card
      size="small"
      className={styles.card}
      title={
        <span className={styles.title}>
          <QuestionCircleOutlined />
          {t("tool.userInput.title", "Codex needs your input")}
        </span>
      }
    >
      <fieldset className={styles.questions} disabled={inactive || submitting}>
        {questions.map((question) => {
          const hasOptions = Boolean(question.options?.length);
          const selected = answers[question.id] ?? "";
          return (
            <div className={styles.question} key={question.id}>
              {question.header ? (
                <Text className={styles.header}>{question.header}</Text>
              ) : null}
              <Text className={styles.prompt}>{question.question}</Text>
              {hasOptions ? (
                <Radio.Group
                  className={styles.options}
                  value={selected || undefined}
                  onChange={(event) =>
                    setAnswers((current) => ({
                      ...current,
                      [question.id]: event.target.value,
                    }))
                  }
                >
                  {question.options?.map((option) => (
                    <Radio key={option.label} value={option.label}>
                      <span className={styles.optionLabel}>{option.label}</span>
                      {option.description ? (
                        <span className={styles.optionDescription}>
                          {option.description}
                        </span>
                      ) : null}
                    </Radio>
                  ))}
                  {question.isOther ? (
                    <Radio value={OTHER_VALUE}>
                      {t("tool.userInput.other", "Other")}
                    </Radio>
                  ) : null}
                </Radio.Group>
              ) : (
                <Input
                  aria-label={question.question}
                  type={question.isSecret ? "password" : "text"}
                  value={selected}
                  onChange={(event) =>
                    setAnswers((current) => ({
                      ...current,
                      [question.id]: event.target.value,
                    }))
                  }
                />
              )}
              {hasOptions && selected === OTHER_VALUE ? (
                <Input
                  className={styles.otherInput}
                  aria-label={t("tool.userInput.customAnswer", "Custom answer")}
                  value={otherAnswers[question.id] ?? ""}
                  onChange={(event) =>
                    setOtherAnswers((current) => ({
                      ...current,
                      [question.id]: event.target.value,
                    }))
                  }
                />
              ) : null}
            </div>
          );
        })}
      </fieldset>

      {effectiveState === "answered" ? (
        <Alert
          className={styles.notice}
          type="success"
          showIcon
          message={t("tool.userInput.submitted", "Answer submitted")}
        />
      ) : null}
      {effectiveState === "expired" || effectiveState === "cancelled" ? (
        <Alert
          className={styles.notice}
          type="warning"
          showIcon
          message={expiredMessage}
        />
      ) : null}
      {error ? (
        <Alert
          className={styles.notice}
          type="error"
          showIcon
          message={error}
        />
      ) : null}

      <div className={styles.actions}>
        <Button
          type="primary"
          loading={submitting}
          disabled={!complete || inactive || !requestId || !sessionId}
          onClick={() => void submit()}
        >
          {t("tool.userInput.submit", "Submit answer")}
        </Button>
      </div>
    </Card>
  );
};

export default RequestUserInputCard;
