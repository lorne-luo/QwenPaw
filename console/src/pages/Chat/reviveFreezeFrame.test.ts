import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { holdView, REVIVE_HOLD_MAX_MS } from "./reviveFreezeFrame";

let frames: Map<number, FrameRequestCallback>;
let nextFrameId: number;

/** Run the callbacks queued for the next animation frame. */
function flushFrame(): void {
  const queued = [...frames.values()];
  frames.clear();
  queued.forEach((callback) => callback(performance.now()));
}

function buildSource(): HTMLElement {
  const source = document.createElement("div");
  source.innerHTML = `
    <div class="messages"><p>hello</p><p>world</p></div>
    <textarea></textarea>
  `;
  document.body.appendChild(source);
  return source;
}

beforeEach(() => {
  frames = new Map();
  nextFrameId = 0;
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    nextFrameId += 1;
    frames.set(nextFrameId, cb);
    return nextFrameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    frames.delete(id);
  });
});

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("holdView", () => {
  it("shows a clone of the sources with their scroll and input state", () => {
    const source = buildSource();
    const list = source.querySelector<HTMLElement>(".messages")!;
    list.scrollTop = -120;
    list.scrollLeft = 4;
    source.querySelector("textarea")!.value = "draft";
    const host = document.createElement("div");

    holdView({ host, sources: [source], isReady: () => false });

    expect(host.children).toHaveLength(1);
    const clone = host.firstElementChild as HTMLElement;
    expect(clone).not.toBe(source);
    expect(clone.textContent).toContain("hello");
    const clonedList = clone.querySelector<HTMLElement>(".messages")!;
    expect(clonedList.scrollTop).toBe(-120);
    expect(clonedList.scrollLeft).toBe(4);
    expect(clone.querySelector("textarea")!.value).toBe("draft");
    expect(source.isConnected).toBe(true);
  });

  it("holds a view whose file input has a selected file", () => {
    const source = buildSource();
    const file = document.createElement("input");
    file.type = "file";
    // A selected file reads back as a fake path, which may not be assigned.
    Object.defineProperty(file, "value", { get: () => "C:\\fakepath\\a.png" });
    source.appendChild(file);
    const host = document.createElement("div");

    const release = holdView({ host, sources: [source], isReady: () => false });

    expect(host.children).toHaveLength(1);
    release();
    expect(host.children).toHaveLength(0);
  });

  it("makes the clone inert so pointer events land on the host", () => {
    const source = buildSource();
    const host = document.createElement("div");

    holdView({ host, sources: [source], isReady: () => false });

    expect(host.firstElementChild!.hasAttribute("inert")).toBe(true);
    expect(source.hasAttribute("inert")).toBe(false);
  });

  it("holds while not ready and releases one frame after ready", () => {
    const host = document.createElement("div");
    let ready = false;

    holdView({ host, sources: [buildSource()], isReady: () => ready });

    flushFrame();
    flushFrame();
    expect(host.children).toHaveLength(1);

    ready = true;
    flushFrame();
    expect(host.children).toHaveLength(1);

    flushFrame();
    expect(host.children).toHaveLength(0);
    expect(frames.size).toBe(0);
  });

  it("releases at the timeout when the new view never becomes ready", () => {
    const host = document.createElement("div");

    holdView({ host, sources: [buildSource()], isReady: () => false });

    vi.advanceTimersByTime(REVIVE_HOLD_MAX_MS - 1);
    flushFrame();
    expect(host.children).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(host.children).toHaveLength(0);
    expect(frames.size).toBe(0);
  });

  it("release is idempotent and cancels the frame loop and timer", () => {
    const host = document.createElement("div");
    const isReady = vi.fn(() => false);

    const release = holdView({ host, sources: [buildSource()], isReady });
    release();
    release();

    expect(host.children).toHaveLength(0);
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    flushFrame();
    expect(isReady).not.toHaveBeenCalled();
  });
});

describe("holdView stylesheets", () => {
  function buildStyleRoot(...css: string[]): HTMLElement {
    const root = document.createElement("div");
    css.forEach((text, index) => {
      const style = document.createElement("style");
      style.id = `s${index}`;
      style.textContent = text;
      root.appendChild(style);
    });
    document.body.appendChild(root);
    return root;
  }

  const hold = (styleRoot: HTMLElement) =>
    holdView({
      host: document.createElement("div"),
      sources: [buildSource()],
      isReady: () => false,
      styleRoot,
    });

  // Mutation records are delivered in a microtask.
  const settle = () => Promise.resolve();

  it("puts a sheet removed during the hold back in place until release", async () => {
    const root = buildStyleRoot(".a{color:red}", ".b{color:blue}", ".c{}");

    const release = hold(root);
    root.querySelector("#s1")!.remove();
    await settle();

    const kept = [...root.children];
    expect(kept.map((el) => el.id || "copy")).toEqual(["s0", "copy", "s2"]);
    expect(kept[1].textContent).toContain(".b");

    release();
    expect([...root.children].map((el) => el.id)).toEqual(["s0", "s2"]);
  });

  it("keeps the rules of a sheet injected through CSSOM", async () => {
    const root = buildStyleRoot();
    const style = document.createElement("style");
    root.appendChild(style);
    style.sheet!.insertRule(".cssom { color: red; }");
    expect(style.textContent).toBe("");

    hold(root);
    style.remove();
    await settle();

    expect(root.children).toHaveLength(1);
    expect(root.firstElementChild!.textContent).toContain(".cssom");
  });

  it("stops keeping sheets after release", async () => {
    const root = buildStyleRoot(".a{}");

    hold(root)();
    root.querySelector("#s0")!.remove();
    await settle();

    expect(root.children).toHaveLength(0);
  });

  it("does not copy its own copies", async () => {
    const root = buildStyleRoot(".a{}");

    hold(root);
    root.querySelector("#s0")!.remove();
    await settle();
    root.firstElementChild!.remove();
    await settle();

    expect(root.children).toHaveLength(0);
  });
});
