/**
 * The chat composer is a Lexical contenteditable with a hidden textarea
 * behind it holding the form state. Focusing the textarea gives no visible
 * caret, so the contenteditable is the element that has to receive focus --
 * these tests pin that ordering.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { focusChatComposer, focusChatComposerWhenReady } from "./utils";

/** jsdom leaves offsetParent null for everything; fake it per element. */
function makeVisible(el: HTMLElement, visible = true) {
  Object.defineProperty(el, "offsetParent", {
    configurable: true,
    get: () => (visible ? document.body : null),
  });
}

function mountSender({
  rich,
  visible = true,
}: {
  rich: boolean;
  visible?: boolean;
}) {
  document.body.innerHTML = "";
  const sender = document.createElement("div");
  sender.className = "ant-sender-wrapper";
  const textarea = document.createElement("textarea");
  sender.appendChild(textarea);
  let editor: HTMLElement | null = null;
  if (rich) {
    editor = document.createElement("div");
    editor.setAttribute("contenteditable", "true");
    editor.tabIndex = 0;
    sender.appendChild(editor);
    makeVisible(editor, visible);
    makeVisible(textarea, false); // hidden state mirror
  } else {
    makeVisible(textarea, visible);
  }
  document.body.appendChild(sender);
  return { textarea, editor };
}

describe("focusChatComposer", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("focuses the contenteditable, not the hidden textarea", () => {
    const { editor, textarea } = mountSender({ rich: true });

    expect(focusChatComposer()).toBe(true);
    expect(document.activeElement).toBe(editor);
    expect(document.activeElement).not.toBe(textarea);
  });

  it("falls back to a plain textarea sender", () => {
    const { textarea } = mountSender({ rich: false });

    expect(focusChatComposer()).toBe(true);
    expect(document.activeElement).toBe(textarea);
  });

  it("reports false when no composer is mounted", () => {
    expect(focusChatComposer()).toBe(false);
  });

  it("skips an invisible composer", () => {
    mountSender({ rich: true, visible: false });
    expect(focusChatComposer()).toBe(false);
  });
});

describe("focusChatComposerWhenReady", () => {
  let frame: () => void;

  beforeEach(() => {
    document.body.innerHTML = "";
    vi.stubGlobal("requestAnimationFrame", (cb: () => void) => {
      frame = cb;
      return 1;
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("retries until the composer mounts", () => {
    focusChatComposerWhenReady();
    frame(); // nothing in the DOM yet
    expect(document.activeElement).toBe(document.body);

    const { editor } = mountSender({ rich: true });
    frame();

    expect(document.activeElement).toBe(editor);
  });

  it("gives up after the frame budget instead of looping forever", () => {
    focusChatComposerWhenReady(2);
    frame();
    frame();
    const spy = vi.fn();
    vi.stubGlobal("requestAnimationFrame", spy);
    frame();
    expect(spy).not.toHaveBeenCalled();
  });
});
