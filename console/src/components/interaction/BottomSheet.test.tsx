// @vitest-environment jsdom
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BottomSheet from "./BottomSheet";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("vaul", () => {
  const Pass = ({ children }: { children?: ReactNode }) => <>{children}</>;
  return {
    Drawer: {
      Root: Pass,
      Portal: Pass,
      Overlay: () => null,
      Content: Pass,
      Title: Pass,
      Description: Pass,
      Handle: () => null,
      Close: Pass,
    },
  };
});

function Sheets({ a, b }: { a: boolean; b: boolean }) {
  return (
    <>
      <BottomSheet open={a} onOpenChange={() => {}} title="A">
        a
      </BottomSheet>
      <BottomSheet open={b} onOpenChange={() => {}} title="B">
        b
      </BottomSheet>
    </>
  );
}

describe("BottomSheet scroll lock", () => {
  afterEach(() => {
    document.documentElement.style.overflow = "";
  });

  it("restores the page overflow after nested sheets close out of order", () => {
    const root = document.documentElement;
    root.style.overflow = "auto";
    const view = render(<Sheets a={false} b={false} />);

    view.rerender(<Sheets a b={false} />);
    view.rerender(<Sheets a b />);
    expect(root.style.overflow).toBe("hidden");

    // Close the first-opened sheet before the second one.
    view.rerender(<Sheets a={false} b />);
    expect(root.style.overflow).toBe("hidden");
    view.rerender(<Sheets a={false} b={false} />);

    expect(root.style.overflow).toBe("auto");
    expect(root.hasAttribute("data-qwp-sheet-lock")).toBe(false);
  });
});
