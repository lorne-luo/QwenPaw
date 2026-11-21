import { describe, expect, it } from "vitest";
import {
  requestText,
  rewindTargetAt,
  rewindTargetOf,
  userIndexOf,
} from "./chatRewind";

const historyCard = (id: string, text: string, clientId?: string) => ({
  input: [
    {
      content: [{ type: "text", text }],
      metadata: {
        original_id: id,
        metadata: clientId ? { qwenpaw_client_message_id: clientId } : {},
      },
    },
  ],
});

const liveCard = (text: string) => ({
  input: [{ content: [{ type: "text", text }] }],
});

const user = (data: unknown) => ({ role: "user", cards: [{ data }] });
const reply = { role: "assistant", cards: [{ data: {} }] };

describe("rewindTargetOf", () => {
  it("prefers the persisted message id", () => {
    expect(rewindTargetOf(historyCard("m1", "hi", "c1"))).toEqual({
      message_id: "m1",
    });
  });

  it("falls back to the client message id", () => {
    const data = {
      input: [{ metadata: { metadata: { qwenpaw_client_message_id: "c1" } } }],
    };
    expect(rewindTargetOf(data)).toEqual({ client_message_id: "c1" });
  });

  it("has no target for a prompt sent live", () => {
    expect(rewindTargetOf(liveCard("hi"))).toBeNull();
    expect(rewindTargetOf(undefined)).toBeNull();
  });
});

describe("live prompts map onto fresh history by position", () => {
  const first = liveCard("one");
  const second = liveCard("two");
  const shown = [user(first), reply, user(second), reply];

  it("finds the card among user messages only", () => {
    expect(userIndexOf(shown, second)).toBe(1);
    expect(userIndexOf(shown, liveCard("two"))).toBe(-1);
  });

  it("takes the same user message from history when the text matches", () => {
    const fresh = [
      user(historyCard("m1", "one")),
      reply,
      user(historyCard("m2", "two")),
      reply,
    ];
    expect(rewindTargetAt(fresh, 1, requestText(second))).toEqual({
      message_id: "m2",
    });
  });

  it("refuses when the lists diverged", () => {
    const fresh = [user(historyCard("m1", "one")), reply];
    expect(rewindTargetAt(fresh, 1, "two")).toBeNull();
    expect(rewindTargetAt(fresh, 0, "two")).toBeNull();
  });
});
