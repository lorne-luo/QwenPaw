/**
 * useQuickCreate — page-side receiver for the sidebar "+" quick-create button.
 *
 * Pages that own a create drawer/modal call this with their sidebar menu id
 * and the handler that opens it. Both delivery channels are handled here:
 * the live window event (page already mounted) and the sessionStorage flag
 * (page mounted as a result of the sidebar navigating here).
 *
 * See layouts/registry/quickCreate.ts for the sidebar side.
 */
import { useEffect, useRef } from "react";
import {
  QUICK_CREATE_EVENT,
  QUICK_CREATE_PENDING_KEY,
} from "../layouts/registry/quickCreate";

export function useQuickCreate(menuId: string, onTrigger: () => void): void {
  const handlerRef = useRef(onTrigger);

  // Declared first so the ref is current before the subscribe effect below
  // runs on mount (effects fire in declaration order).
  useEffect(() => {
    handlerRef.current = onTrigger;
  }, [onTrigger]);

  useEffect(() => {
    if (sessionStorage.getItem(QUICK_CREATE_PENDING_KEY) === menuId) {
      sessionStorage.removeItem(QUICK_CREATE_PENDING_KEY);
      handlerRef.current();
    }

    const listener = (event: Event) => {
      const detail = (event as CustomEvent<{ menuId?: string }>).detail;
      if (detail?.menuId === menuId) handlerRef.current();
    };
    window.addEventListener(QUICK_CREATE_EVENT, listener);
    return () => window.removeEventListener(QUICK_CREATE_EVENT, listener);
  }, [menuId]);
}
