import { request } from "../request";

export type HarnessUserInputAnswers = Record<string, { answers: string[] }>;

export const harnessUserInputApi = {
  submit: (
    providerId: string,
    requestId: string,
    sessionId: string,
    answers: HarnessUserInputAnswers,
  ) =>
    request<{ ok: boolean }>(
      `/harnesses/${encodeURIComponent(
        providerId,
      )}/user-input/${encodeURIComponent(requestId)}`,
      {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, answers }),
      },
    ),
};
