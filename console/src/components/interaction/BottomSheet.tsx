import { useEffect, useState, type ReactNode } from "react";
import { Drawer } from "vaul";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import styles from "./BottomSheet.module.less";

/**
 * vaul only locks the document body, and this app keeps `noBodyStyles` on so
 * vaul never touches body layout (the OS shell relies on it). Underlying
 * scrollers (.os-window-body, .page-container, html) would therefore keep
 * scrolling behind an open sheet. Count nested open sheets and expose the
 * depth on <html> so global CSS can freeze those scrollers.
 */
let openSheetCount = 0;
// The overflow before the first sheet opened; nested sheets share one lock,
// so only the outermost acquire records it and only the last release restores.
let unlockedOverflow = "";

function acquireSheetScrollLock() {
  const root = document.documentElement;
  if (openSheetCount === 0) {
    unlockedOverflow = root.style.overflow;
    root.setAttribute("data-qwp-sheet-lock", "");
    root.style.overflow = "hidden";
  }
  openSheetCount += 1;
  return () => {
    openSheetCount -= 1;
    if (openSheetCount > 0) return;
    root.removeAttribute("data-qwp-sheet-lock");
    root.style.overflow = unlockedOverflow;
  };
}

export default function BottomSheet({
  open,
  onOpenChange,
  title,
  children,
  footer,
  tall = false,
  initialSnap = 0.9,
  onCloseFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  tall?: boolean;
  initialSnap?: number;
  onCloseFocus?: () => void;
}) {
  const { t } = useTranslation();
  const [snap, setSnap] = useState<number | string | null>(initialSnap);
  // vaul resets the active snap point to the smallest one after the sheet
  // closes (and syncs it back into our controlled state), so reopening any
  // editor previously came back at 50% height. Snap back to the caller's
  // preferred height every time the sheet opens.
  useEffect(() => {
    if (open) setSnap(initialSnap);
  }, [open, initialSnap]);
  useEffect(() => {
    if (!open) return undefined;
    return acquireSheetScrollLock();
  }, [open]);
  return (
    <Drawer.Root
      open={open}
      onOpenChange={onOpenChange}
      snapPoints={tall ? [0.6, 0.9] : undefined}
      activeSnapPoint={tall ? snap : undefined}
      setActiveSnapPoint={setSnap}
      fixed
      repositionInputs
      noBodyStyles
      autoFocus
    >
      <Drawer.Portal>
        <Drawer.Overlay className={styles.overlay} />
        <Drawer.Content
          className={styles.sheet}
          aria-describedby={undefined}
          style={tall ? { height: "100dvh", maxHeight: "100dvh" } : undefined}
          onCloseAutoFocus={(event) => {
            if (onCloseFocus) {
              event.preventDefault();
              onCloseFocus();
            }
          }}
        >
          <Drawer.Handle className={styles.handle} />
          <header className={styles.header}>
            <Drawer.Title>{title}</Drawer.Title>
            <Drawer.Close aria-label={t("common.close")}>
              <X size={18} />
            </Drawer.Close>
          </header>
          <div
            className={styles.viewport}
            style={
              tall && typeof snap === "number"
                ? { maxHeight: `calc(${snap * 100}dvh - 68px)` }
                : undefined
            }
          >
            <div className={styles.content}>{children}</div>
            {footer && <div className={styles.footer}>{footer}</div>}
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}
