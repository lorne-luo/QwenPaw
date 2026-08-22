/** Longest time the previous view is held while the new runtime loads. */
export const REVIVE_HOLD_MAX_MS = 3_000;

export type HoldViewOptions = {
  /** Empty overlay host; the snapshot is cloned into it. */
  host: HTMLElement;
  /** Current runtime DOM to snapshot. */
  sources: HTMLElement[];
  /** True once the new runtime has rendered its history. */
  isReady: () => boolean;
  timeoutMs?: number;
  /** Where the runtime's stylesheets live; removed ones are kept until release. */
  styleRoot?: HTMLElement;
};

type LiveState = {
  index: number;
  scrollTop: number;
  scrollLeft: number;
  value?: string;
};

/** Scroll offsets and form values, which `cloneNode` does not carry over. */
function readLiveState(root: Element): LiveState[] {
  const states: LiveState[] = [];
  [root, ...root.querySelectorAll("*")].forEach((element, index) => {
    const { scrollTop, scrollLeft } = element;
    // A file input's value is a read-only fake path; assigning it throws.
    const value =
      element instanceof HTMLTextAreaElement ||
      (element instanceof HTMLInputElement && element.type !== "file")
        ? element.value
        : undefined;
    if (scrollTop || scrollLeft || value !== undefined) {
      states.push({ index, scrollTop, scrollLeft, value });
    }
  });
  return states;
}

function applyLiveState(root: Element, states: LiveState[]): void {
  // A deep clone has the same element order as its source.
  const elements = [root, ...root.querySelectorAll("*")];
  for (const { index, scrollTop, scrollLeft, value } of states) {
    const element = elements[index];
    if (value !== undefined) {
      (element as HTMLTextAreaElement | HTMLInputElement).value = value;
    }
    element.scrollTop = scrollTop;
    element.scrollLeft = scrollLeft;
  }
}

/**
 * Keep every stylesheet that is removed from `root` until the returned
 * function runs. Unmounting the old runtime drops its CSS-in-JS sheets
 * (emotion globals, antd css-var tokens) and the new runtime injects its own
 * under a different token class, so without this the copy loses its styling
 * for the rest of the hold. Each removed sheet comes back as an anonymous copy
 * at its old position; libraries look their sheets up by data attributes, so
 * they never pick up a copy.
 */
function keepRemovedStyles(root: HTMLElement): () => void {
  // A sheet injected through CSSOM has no text, and its rules are gone once
  // the element is removed, so they are read now.
  const rulesOf = new Map<Node, string>();
  root.querySelectorAll("style").forEach((style) => {
    if (style.textContent || !style.sheet) return;
    rulesOf.set(
      style,
      Array.from(style.sheet.cssRules, (rule) => rule.cssText).join("\n"),
    );
  });
  const copies = new Set<Node>();
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      record.removedNodes.forEach((node) => {
        if (!(node instanceof HTMLStyleElement) || copies.has(node)) return;
        const copy = document.createElement("style");
        copy.dataset.reviveHold = "";
        copy.textContent = rulesOf.get(node) ?? node.textContent;
        const { nextSibling: next, previousSibling: prev } = record;
        if (next?.parentNode === root) {
          root.insertBefore(copy, next);
        } else if (prev?.parentNode === root) {
          root.insertBefore(copy, prev.nextSibling);
        } else {
          root.appendChild(copy);
        }
        copies.add(copy);
      });
    }
  });
  observer.observe(root, { childList: true });
  return () => {
    observer.disconnect();
    copies.forEach((copy) => copy.parentNode?.removeChild(copy));
  };
}

/**
 * Cover the chat with a static copy of its current DOM while the runtime
 * remounts underneath, so no empty frame is painted. The copy is released one
 * frame after `isReady()` turns true (the new history has painted by then), or
 * after `timeoutMs` at the latest. Returns an idempotent release function.
 */
export function holdView({
  host,
  sources,
  isReady,
  timeoutMs = REVIVE_HOLD_MAX_MS,
  styleRoot = document.head,
}: HoldViewOptions): () => void {
  const releaseStyles = keepRemovedStyles(styleRoot);
  // Read everything before inserting the clones: one layout pass, not one
  // per element.
  const states = sources.map(readLiveState);
  const clones = sources.map((source) => {
    const clone = source.cloneNode(true) as HTMLElement;
    // Taps land on the host instead of focusing a cloned control.
    clone.setAttribute("inert", "");
    return clone;
  });
  host.replaceChildren(...clones);
  clones.forEach((clone, i) => applyLiveState(clone, states[i]));

  let released = false;
  let frame = 0;
  const release = () => {
    if (released) return;
    released = true;
    cancelAnimationFrame(frame);
    clearTimeout(timer);
    host.replaceChildren();
    releaseStyles();
  };
  const timer = setTimeout(release, timeoutMs);
  const poll = () => {
    frame = requestAnimationFrame(isReady() ? release : poll);
  };
  frame = requestAnimationFrame(poll);
  return release;
}
