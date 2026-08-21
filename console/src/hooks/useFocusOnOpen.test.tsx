/**
 * The sidebar "+" is only useful if the caret actually lands in the first
 * field. These tests pin that against real antd Modal/Drawer instances,
 * including the baseline that motivated the hook: plain `autoFocus` does NOT
 * survive an antd overlay, so nothing here may quietly regress to it.
 */
import { describe, it, expect } from "vitest";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Drawer, Input, Modal } from "antd";
import { useFocusOnOpen } from "./useFocusOnOpen";

function Harness({
  kind,
  enabled = true,
  cursor,
  initial = "",
  destroy = true,
}: {
  kind: "modal" | "drawer";
  enabled?: boolean;
  cursor?: "start" | "end" | "all";
  initial?: string;
  destroy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const fieldRef = useFocusOnOpen(open, { enabled, cursor });
  const field = (
    <Input ref={fieldRef} aria-label="field" defaultValue={initial} />
  );
  return (
    <>
      <button onClick={() => setOpen(true)}>open</button>
      <button onClick={() => setOpen(false)}>close</button>
      {kind === "modal" ? (
        <Modal open={open} destroyOnHidden={destroy} footer={null}>
          {field}
        </Modal>
      ) : (
        <Drawer open={open} destroyOnHidden={destroy}>
          {field}
        </Drawer>
      )}
    </>
  );
}

describe.each(["modal", "drawer"] as const)("useFocusOnOpen in %s", (kind) => {
  it("focuses the field on open", async () => {
    render(<Harness kind={kind} />);

    await userEvent.click(screen.getByText("open"));

    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("field")),
    );
  });

  it("focuses again on a second open", async () => {
    render(<Harness kind={kind} />);

    await userEvent.click(screen.getByText("open"));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("field")),
    );
    await userEvent.click(screen.getByText("close"));
    await userEvent.click(screen.getByText("open"));

    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("field")),
    );
  });

  it("stays out of the way when disabled", async () => {
    render(<Harness kind={kind} enabled={false} />);
    const opener = screen.getByText("open");

    await userEvent.click(opener);
    await screen.findByLabelText("field");

    expect(document.activeElement).not.toBe(screen.getByLabelText("field"));
  });

  it("focuses on reopen even without destroyOnHidden", async () => {
    // AgentModal and the MCP create modal keep their bodies mounted, so the
    // hook must not depend on the field remounting.
    render(<Harness kind={kind} destroy={false} />);

    await userEvent.click(screen.getByText("open"));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("field")),
    );
    await userEvent.click(screen.getByText("close"));
    (document.activeElement as HTMLElement)?.blur();
    await userEvent.click(screen.getByText("open"));

    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("field")),
    );
  });

  it("selects a pre-filled value with cursor: all", async () => {
    render(<Harness kind={kind} cursor="all" initial="template" />);

    await userEvent.click(screen.getByText("open"));

    const field = (await screen.findByLabelText("field")) as HTMLInputElement;
    await waitFor(() => expect(document.activeElement).toBe(field));
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe("template".length);
  });
});

describe("baseline that motivated the hook", () => {
  it("plain autoFocus does not survive an antd Modal", async () => {
    function AutoFocusHarness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>open</button>
          <Modal open={open} destroyOnHidden footer={null}>
            <Input autoFocus aria-label="field" />
          </Modal>
        </>
      );
    }
    render(<AutoFocusHarness />);

    await userEvent.click(screen.getByText("open"));
    const field = await screen.findByLabelText("field");

    // Documents why every create surface uses the hook instead.
    expect(document.activeElement).not.toBe(field);
  });
});
