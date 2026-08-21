/**
 * useFocusOnOpen — put the caret in a Modal/Drawer's first field on open.
 *
 * Two simpler mechanisms were tried and rejected:
 *
 *  - plain `autoFocus`: React fires it while the dialog is still in rc-motion's
 *    "prepare" stage, the focus call is dropped, and nothing re-focuses the
 *    field afterwards. See useFocusOnOpen.test.tsx, which pins this so nobody
 *    "simplifies" back to it.
 *  - the overlay's `afterOpenChange`: fires for Drawer but is driven by the
 *    zoom transition for Modal, so it is not a dependable single mechanism.
 *
 * Driving off the `open` flag instead is independent of motion. The field is
 * not focusable on the first frame, so focus is retried for a few frames and
 * stops as soon as it lands.
 *
 *   const focusName = useFocusOnOpen(open);
 *   <Drawer open={open}><Input ref={focusName} /></Drawer>
 *
 * A ref callback is returned rather than a ref object: antd types its `ref`
 * prop against the concrete `InputRef` / `TextAreaRef`, and a `RefObject` of
 * the structural `FocusableField` is not assignable to those, so every call
 * site would have to name its own field type. A callback is.
 *
 * Pass `cursor: "all"` for pre-filled fields, where leaving the caret at
 * offset 0 would mean the first keystroke corrupts the existing text.
 */
import { useCallback, useEffect, useRef } from "react";

/**
 * Structural shape of antd's Input / Input.TextArea refs — `focus` to move the
 * caret, plus the underlying node under whichever key that component uses, so
 * we can tell whether focus actually landed.
 */
export interface FocusableField {
  focus: (options?: { cursor?: "start" | "end" | "all" }) => void;
  input?: HTMLInputElement | null;
  nativeElement?: HTMLElement | null;
  resizableTextArea?: { textArea?: HTMLTextAreaElement | null };
}

export interface UseFocusOnOpenOptions {
  /** Where to leave the caret. Omit for antd's default (end of value). */
  cursor?: "start" | "end" | "all";
  /**
   * Skip focusing when false — e.g. the field is read-only in edit mode, or a
   * different tab is showing.
   */
  enabled?: boolean;
  /** How many frames to keep retrying before giving up. */
  maxFrames?: number;
}

/** The DOM node behind a field ref, whichever key the component exposes. */
function resolveNode(field: FocusableField): HTMLElement | null {
  return (
    field.input ??
    field.resizableTextArea?.textArea ??
    field.nativeElement ??
    null
  );
}

export function useFocusOnOpen(
  open: boolean,
  options: UseFocusOnOpenOptions = {},
): React.RefCallback<FocusableField> {
  const { cursor, enabled = true, maxFrames = 12 } = options;
  const ref = useRef<FocusableField | null>(null);
  // Stable, so a re-render does not detach and re-attach the field.
  const setField = useCallback((field: FocusableField | null) => {
    ref.current = field;
  }, []);

  useEffect(() => {
    if (!open || !enabled) return;

    let remaining = maxFrames;
    let frame = requestAnimationFrame(function attempt() {
      const field = ref.current;
      if (field) {
        field.focus(cursor ? { cursor } : undefined);
        const node = resolveNode(field);
        // Landed (or the component exposes no node to check against, in which
        // case one focus call is all we can meaningfully do).
        if (!node || document.activeElement === node) return;
      }
      if (--remaining <= 0) return;
      frame = requestAnimationFrame(attempt);
    });

    return () => cancelAnimationFrame(frame);
  }, [open, enabled, cursor, maxFrames]);

  return setField;
}
