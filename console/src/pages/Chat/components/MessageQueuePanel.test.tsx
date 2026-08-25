import { describe, expect, it, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/common_setup";
import type { QueueItem } from "../../../stores/messageQueueStore";
import MessageQueuePanel from "./MessageQueuePanel";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) =>
      typeof fallback === "string" ? fallback : key,
  }),
}));

vi.mock("../../../contexts/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));

const isMobile = vi.fn(() => false);
vi.mock("../../../hooks/useIsMobile", () => ({
  useIsMobile: () => isMobile(),
}));

const item = (overrides: Partial<QueueItem> = {}): QueueItem => ({
  id: "mq-1",
  text: "queued message",
  status: "pending",
  retryCount: 0,
  createdAt: 1,
  agentId: "agent-1",
  bizParams: {},
  ...overrides,
});

const handlers = () => ({
  onRemove: vi.fn(),
  onEdit: vi.fn(),
  onReorder: vi.fn(),
  onInterruptAndSend: vi.fn(),
  onClear: vi.fn(),
  onPauseResume: vi.fn(),
  onRetry: vi.fn(),
  onSkip: vi.fn(),
});

const renderPanel = (items: QueueItem[] = [item()]) => {
  const props = handlers();
  const view = renderWithProviders(
    <MessageQueuePanel items={items} runState="idle" {...props} />,
  );
  return { ...view, props };
};

/** The row's action buttons all share one wrapper; delete is the last of them. */
const actionRow = (container: HTMLElement) => {
  const buttons = container.querySelectorAll("button");
  expect(buttons.length).toBeGreaterThan(0);
  return buttons[buttons.length - 1].parentElement as HTMLElement;
};

describe("MessageQueuePanel row actions", () => {
  beforeEach(() => {
    isMobile.mockReturnValue(false);
    vi.clearAllMocks();
  });

  it("hides the actions behind hover on desktop", () => {
    const { container } = renderPanel();

    const row = actionRow(container);
    expect(row.style.pointerEvents).toBe("none");
    expect(row.style.opacity).toBe("0");
  });

  it("keeps the actions reachable on touch, where there is no hover", () => {
    isMobile.mockReturnValue(true);
    const { container } = renderPanel();

    const row = actionRow(container);
    expect(row.style.pointerEvents).toBe("auto");
    expect(row.style.opacity).toBe("1");
  });

  it("lets a touch user interrupt and send", async () => {
    isMobile.mockReturnValue(true);
    const queued = item();
    const { container, props } = renderPanel([queued]);

    // Row buttons for a pending item: edit, interrupt-and-send, delete.
    const buttons = Array.from(container.querySelectorAll("button"));
    const interrupt = buttons[buttons.length - 2];
    await userEvent.click(interrupt);

    expect(props.onInterruptAndSend).toHaveBeenCalledWith(queued);
  });

  it("does not offer native drag on touch", () => {
    isMobile.mockReturnValue(true);
    const { container } = renderPanel();

    expect(container.querySelector('[draggable="true"]')).toBeNull();
  });

  it("still offers native drag on desktop", () => {
    const { container } = renderPanel();

    expect(container.querySelector('[draggable="true"]')).not.toBeNull();
  });
});
