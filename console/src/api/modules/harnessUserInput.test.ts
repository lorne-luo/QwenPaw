import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../request", () => ({
  request: vi.fn(),
}));

import { request } from "../request";
import { harnessUserInputApi } from "./harnessUserInput";

describe("harnessUserInputApi", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("posts answers to the host harness user-input route", async () => {
    vi.mocked(request).mockResolvedValue({ ok: true });

    await harnessUserInputApi.submit("codex", "req/1", "chat-1", {
      choice: { answers: ["Second"] },
    });

    expect(request).toHaveBeenCalledWith(
      "/harnesses/codex/user-input/req%2F1",
      {
        method: "POST",
        body: JSON.stringify({
          session_id: "chat-1",
          answers: { choice: { answers: ["Second"] } },
        }),
      },
    );
  });
});
