// @vitest-environment jsdom
/**
 * SidebarSessionList render tests — regression family: session state ×
 * navigation combos (bug_insights highest-frequency cluster, ~15 bugs)
 * and cross-agent switch isolation.
 *
 * Strategy: stub VariableSizeList to render every row directly (jsdom has
 * no layout engine, so the real virtualized list renders nothing), and
 * stub the DnD wrappers as pass-throughs. Heavy hooks are mocked so the
 * row-rendering logic (VirtualRow / GroupHeaderContent / date headers)
 * executes under test.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { renderWithProviders } from "@/test/common_setup";

// ---- Hoisted mocks ---------------------------------------------------------

const mockSessionListData = vi.hoisted(() => vi.fn());
const mockChatGroups = vi.hoisted(() => vi.fn());
const mockCollapsedGroups = vi.hoisted(() => vi.fn());
const mockSelectedAgent = vi.hoisted(() => ({ current: "agent-1" }));
/** Captures the latest virtual-list props so tests can assert row heights. */
const mockListProps = vi.hoisted(() => ({
  current: null as null | {
    itemCount: number;
    itemSize: (index: number) => number;
  },
}));
const mockUnseenSessionIds = vi.hoisted(() => vi.fn());

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (
      key: string,
      fallback?: string | { defaultValue?: string; name?: string },
    ) =>
      typeof fallback === "object"
        ? (fallback.defaultValue ?? key).replace(
            "{{name}}",
            fallback.name ?? "",
          )
        : fallback ?? key,
    i18n: { language: "en" },
  }),
}));

// react-window: render ALL rows so row logic is covered
vi.mock("react-window", () => ({
  VariableSizeList: React.forwardRef(
    (
      props: {
        itemCount: number;
        itemSize: (index: number) => number;
        itemData: unknown;
        children: React.ComponentType<{
          index: number;
          style?: object;
          data: unknown;
        }>;
      },
      ref: React.Ref<object>,
    ) => {
      React.useImperativeHandle(ref, () => ({
        scrollTo: vi.fn(),
        scrollToItem: vi.fn(),
        resetAfterIndex: vi.fn(),
      }));
      mockListProps.current = {
        itemCount: props.itemCount,
        itemSize: props.itemSize,
      };
      const Row = props.children;
      return (
        <div data-testid="virtual-list">
          {Array.from({ length: props.itemCount }, (_, i) => (
            <Row key={i} index={i} data={props.itemData} style={{}} />
          ))}
        </div>
      );
    },
  ),
}));

// DnD wrappers as pass-throughs
vi.mock("../components/SessionGroupDnd", () => ({
  SessionGroupDndProvider: ({ children }: { children?: React.ReactNode }) => (
    <>{children}</>
  ),
  DraggableSession: ({ children }: { children?: React.ReactNode }) => (
    <>{children}</>
  ),
  SessionDropZone: ({ children }: { children?: React.ReactNode }) => (
    <>{children}</>
  ),
}));

vi.mock("./useSidebarSessionListData", () => ({
  useSessionListData: (...args: unknown[]) => mockSessionListData(...args),
  getBackendId: (s: { realId?: string; id?: string }) =>
    s?.realId ?? s?.id ?? null,
}));

vi.mock("../hooks/useChatGroups", () => ({
  useChatGroups: () => mockChatGroups(),
}));

vi.mock("../hooks/useCollapsedChatGroups", () => ({
  useCollapsedChatGroups: () => mockCollapsedGroups(),
}));

vi.mock("../hooks/useRevealActiveChatGroup", () => ({
  useRevealActiveChatGroup: vi.fn(),
}));

vi.mock("../hooks/useSessionAttention", () => ({
  useSessionAttention: () => mockUnseenSessionIds(),
}));

vi.mock("../components/SessionItem", () => ({
  default: ({
    name,
    sessionId,
    onClick,
  }: {
    name: string;
    sessionId: string;
    onClick: (id: string) => void;
  }) => (
    <button
      data-testid={`session-item-${sessionId}`}
      onClick={() => onClick(sessionId)}
    >
      {name}
    </button>
  ),
}));

vi.mock("../components/SessionGroupHeader", () => ({
  default: ({
    group,
    count,
    collapsed,
    onToggle,
  }: {
    group: { id: string; name: string };
    count: number;
    collapsed: boolean;
    onToggle: () => void;
  }) => (
    <button
      type="button"
      data-testid={`group-header-${group.id}`}
      aria-expanded={!collapsed}
      onClick={onToggle}
    >
      {group.name} {count}
    </button>
  ),
  UnseenGroupHeader: ({
    count,
    onMarkAllSeen,
  }: {
    count: number;
    onMarkAllSeen?: () => void;
  }) => (
    <>
      <div data-testid="unseen-group-header">{count}</div>
      {onMarkAllSeen && (
        <button type="button" onClick={onMarkAllSeen}>
          mark-all-seen
        </button>
      )}
    </>
  ),
}));

vi.mock("../components/SessionDateHeader", () => ({
  default: ({
    label,
    count,
    collapsed,
    onToggle,
  }: {
    label: string;
    count: number;
    collapsed?: boolean;
    onToggle?: () => void;
  }) => (
    <button
      type="button"
      data-testid="date-header"
      aria-expanded={collapsed === undefined ? undefined : !collapsed}
      onClick={onToggle}
    >
      {label} {count}
    </button>
  ),
}));

vi.mock("../utils/channel", () => ({
  getChannelLabel: (key: string) => `channel:${key}`,
}));

vi.mock("../api/modules/chat", () => ({
  chatApi: { updateChat: vi.fn().mockResolvedValue({}) },
}));

vi.mock("../hooks/useAppMessage", () => ({
  useAppMessage: () => ({
    message: {
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      warning: vi.fn(),
    },
  }),
}));

vi.mock("../stores/agentStore", () => {
  const state = () => ({
    selectedAgent: mockSelectedAgent.current,
    agents: [{ id: "agent-1", name: "Research Bot" }],
  });
  return {
    useAgentStore: (selector?: (s: ReturnType<typeof state>) => unknown) =>
      selector ? selector(state()) : state(),
  };
});

vi.mock("../stores/sessionListStore", () => ({
  useSessionListStore: (selector?: (s: { sessions: unknown[] }) => unknown) =>
    selector ? selector({ sessions: [] }) : { sessions: [] },
  syncSessionsGlobal: vi.fn(),
}));

import SidebarSessionList from "./SidebarSessionList";
import { useSessionAttentionStore } from "../stores/sessionAttentionStore";

// ---- Fixtures --------------------------------------------------------------

const sessionA = {
  id: "sess-a",
  name: "Alpha Chat",
  status: "idle",
  generating: false,
  archived: false,
  pinned: false,
  updatedAt: new Date().toISOString(),
  channel: "",
};

const sessionB = {
  id: "sess-b",
  name: "Beta Report",
  status: "running",
  generating: true,
  archived: false,
  pinned: false,
  updatedAt: new Date().toISOString(),
  channel: "wechat",
};

function mockData(
  sessions: unknown[],
  overrides: Record<string, unknown> = {},
) {
  // Forward the injected onSessionClick through the mocked hook so click
  // routing tests observe it.
  mockSessionListData.mockImplementation(
    (
      _store: unknown,
      _set: unknown,
      options?: { onSessionClick?: (id: string) => void },
    ) => ({
      sortedSessions: sessions,
      loading: false,
      editingSessionId: null,
      editValue: "",
      handleSessionClick: (id: string) => options?.onSessionClick?.(id),
      handleEditStart: vi.fn(),
      handleDelete: vi.fn(),
      handleArchiveToggle: vi.fn(),
      handlePinToggle: vi.fn(),
      handleEditChange: vi.fn(),
      handleEditSubmit: vi.fn(),
      handleEditCancel: vi.fn(),
      refreshSessions: vi.fn().mockResolvedValue(undefined),
      ...overrides,
    }),
  );
  mockChatGroups.mockReturnValue({
    // groupChats only emits rows for groups that exist — provide the
    // default "Uncategorized" group so unassigned sessions render.
    groups: [
      {
        id: "default",
        name: "Uncategorized",
        order: 0,
        kind: "default",
        pinned: false,
      },
    ],
    createGroup: vi.fn().mockResolvedValue({ id: "g-new" }),
    renameGroup: vi.fn(),
    pinGroup: vi.fn(),
    deleteGroup: vi.fn(),
    reorderGroups: vi.fn(),
  });
  mockCollapsedGroups.mockReturnValue({
    collapsedGroups: new Set<string>(),
    toggleGroup: vi.fn(),
    expandGroup: vi.fn(),
    initializeCollapsedGroups: vi.fn(),
  });
  mockUnseenSessionIds.mockReturnValue(new Set<string>());
}

describe("SidebarSessionList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mockSelectedAgent.current = "agent-1";
    // The virtual list only renders once the wrapper has a measured height.
    // jsdom reports clientHeight=0, so make ResizeObserver report one
    // immediately on observe. Must be a function (constructible), not an
    // arrow fn.
    global.ResizeObserver = vi.fn().mockImplementation(function (
      this: unknown,
      cb: (entries: { contentRect: { height: number } }[]) => void,
    ) {
      return {
        observe: () => cb([{ contentRect: { height: 600 } }]),
        unobserve: vi.fn(),
        disconnect: vi.fn(),
      };
    }) as unknown as typeof ResizeObserver;
  });

  it("renders the empty state when there are no conversations", async () => {
    mockData([]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByText("No conversations")).toBeTruthy();
    });
  });

  it("renders session rows via the (stubbed) virtual list", async () => {
    mockData([sessionA, sessionB]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("virtual-list")).toBeTruthy();
    });
    expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    expect(screen.getByTestId("session-item-sess-b")).toBeTruthy();
  });

  it("initializes older unpinned groups as collapsed", async () => {
    const initializeCollapsedGroups = vi.fn();
    mockData([sessionA, { ...sessionB, groupId: "work" }]);
    mockCollapsedGroups.mockReturnValue({
      collapsedGroups: new Set<string>(),
      toggleGroup: vi.fn(),
      expandGroup: vi.fn(),
      initializeCollapsedGroups,
    });
    mockChatGroups.mockReturnValue({
      groups: [
        {
          id: "default",
          name: "Uncategorized",
          order: 0,
          kind: "default",
          pinned: false,
        },
        {
          id: "work",
          name: "Work",
          order: 1,
          kind: "custom",
          pinned: false,
        },
        {
          id: "older",
          name: "Older",
          order: 2,
          kind: "custom",
          pinned: true,
        },
        {
          id: "archive",
          name: "Archive",
          order: 3,
          kind: "custom",
          pinned: false,
        },
      ],
      createGroup: vi.fn().mockResolvedValue({ id: "g-new" }),
      renameGroup: vi.fn(),
      pinGroup: vi.fn(),
      deleteGroup: vi.fn(),
      reorderGroups: vi.fn(),
    });
    renderWithProviders(<SidebarSessionList />);

    await waitFor(() => expect(initializeCollapsedGroups).toHaveBeenCalled());
    expect(initializeCollapsedGroups).toHaveBeenCalledWith(
      new Set(["archive"]),
    );
  });

  it("keeps the active group expanded during default initialization", async () => {
    const initializeCollapsedGroups = vi.fn();
    const now = Date.now();
    mockData([
      { ...sessionA, updatedAt: new Date(now).toISOString() },
      {
        ...sessionB,
        groupId: "work",
        updatedAt: new Date(now - 1000).toISOString(),
      },
      {
        ...sessionA,
        id: "older-active",
        groupId: "older",
        updatedAt: new Date(now - 2000).toISOString(),
      },
    ]);
    mockCollapsedGroups.mockReturnValue({
      collapsedGroups: new Set<string>(),
      toggleGroup: vi.fn(),
      expandGroup: vi.fn(),
      initializeCollapsedGroups,
    });
    mockChatGroups.mockReturnValue({
      groups: [
        {
          id: "default",
          name: "Uncategorized",
          order: 0,
          kind: "default",
          pinned: false,
        },
        {
          id: "work",
          name: "Work",
          order: 1,
          kind: "custom",
          pinned: false,
        },
        {
          id: "older",
          name: "Older",
          order: 2,
          kind: "custom",
          pinned: false,
        },
        {
          id: "archive",
          name: "Archive",
          order: 3,
          kind: "custom",
          pinned: false,
        },
      ],
      createGroup: vi.fn().mockResolvedValue({ id: "g-new" }),
      renameGroup: vi.fn(),
      pinGroup: vi.fn(),
      deleteGroup: vi.fn(),
      reorderGroups: vi.fn(),
    });

    renderWithProviders(<SidebarSessionList />, {
      initialEntries: ["/chat/older-active"],
    });

    await waitFor(() => expect(initializeCollapsedGroups).toHaveBeenCalled());
    expect(initializeCollapsedGroups).toHaveBeenCalledWith(
      new Set(["archive"]),
    );
  });

  it("renders every conversation without a load-more control", async () => {
    const sessions = Array.from({ length: 12 }, (_, index) => ({
      ...sessionA,
      id: `session-${index + 1}`,
      name: `Conversation ${index + 1}`,
      updatedAt: new Date(Date.now() - index * 1000).toISOString(),
    }));
    mockData(sessions);
    renderWithProviders(<SidebarSessionList />);

    await waitFor(() => {
      expect(screen.getByTestId("session-item-session-12")).toBeTruthy();
    });
    expect(screen.getByTestId("session-item-session-11")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  });

  it("reveals an active unpinned conversation after ten pinned ones", async () => {
    const pinnedSessions = Array.from({ length: 10 }, (_, index) => ({
      ...sessionA,
      id: `pinned-${index + 1}`,
      name: `Pinned ${index + 1}`,
      pinned: true,
      updatedAt: new Date(Date.now() - (index + 1) * 1000).toISOString(),
    }));
    const activeSession = {
      ...sessionA,
      id: "active-session",
      name: "Active conversation",
      pinned: false,
      updatedAt: new Date().toISOString(),
    };
    mockData([activeSession, ...pinnedSessions]);

    renderWithProviders(<SidebarSessionList />, {
      initialEntries: ["/chat/active-session"],
    });

    await waitFor(() => {
      expect(screen.getByTestId("session-item-active-session")).toBeTruthy();
    });
  });

  it("renders the full history around the active conversation", async () => {
    const sessions = Array.from({ length: 25 }, (_, index) => ({
      ...sessionA,
      id: `session-${index + 1}`,
      name: `Conversation ${index + 1}`,
      updatedAt: new Date(Date.now() - index * 1000).toISOString(),
    }));
    mockData(sessions);
    renderWithProviders(<SidebarSessionList />, {
      initialEntries: ["/chat/session-12"],
    });

    await waitFor(() => {
      expect(screen.getByTestId("session-item-session-12")).toBeTruthy();
    });
    expect(screen.getByTestId("session-item-session-21")).toBeTruthy();
    expect(screen.getByTestId("session-item-session-25")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  });

  it("swaps the rendered list when switching agents", async () => {
    const agentOneSessions = Array.from({ length: 25 }, (_, index) => ({
      ...sessionA,
      id: `agent-one-${index + 1}`,
      name: `Agent one ${index + 1}`,
      updatedAt: new Date(Date.now() - index * 1000).toISOString(),
    }));
    const agentTwoSessions = Array.from({ length: 25 }, (_, index) => ({
      ...sessionA,
      id: `agent-two-${index + 1}`,
      name: `Agent two ${index + 1}`,
      updatedAt: new Date(Date.now() - index * 1000).toISOString(),
    }));

    mockData(agentOneSessions);
    const { rerender } = renderWithProviders(<SidebarSessionList />);

    await waitFor(() => {
      expect(screen.getByTestId("session-item-agent-one-25")).toBeTruthy();
    });

    mockSelectedAgent.current = "agent-2";
    mockData(agentTwoSessions);
    rerender(<SidebarSessionList />);

    await waitFor(() => {
      expect(screen.getByTestId("session-item-agent-two-25")).toBeTruthy();
      expect(screen.queryByTestId("session-item-agent-one-1")).toBeNull();
    });
  });

  it("shows all matching conversations while searching", async () => {
    const sessions = Array.from({ length: 12 }, (_, index) => ({
      ...sessionA,
      id: `session-${index + 1}`,
      name: `Conversation ${index + 1}`,
      updatedAt: new Date(Date.now() - index * 1000).toISOString(),
    }));
    mockData(sessions);
    renderWithProviders(<SidebarSessionList />);

    await waitFor(() => {
      expect(screen.getByTestId("session-item-session-11")).toBeTruthy();
    });
    fireEvent.change(screen.getByPlaceholderText("Search…"), {
      target: { value: "Conversation 1" },
    });

    await waitFor(() => {
      expect(screen.queryByTestId("session-item-session-2")).toBeNull();
    });
    expect(screen.getByTestId("session-item-session-11")).toBeTruthy();
    expect(screen.getByTestId("session-item-session-12")).toBeTruthy();
  });

  it("leads the date view with the unread group too", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "date");
    const unseenChat = { ...sessionA, id: "sess-unseen" };
    mockData([sessionA, unseenChat]);
    mockUnseenSessionIds.mockReturnValue(new Set(["sess-unseen"]));
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("unseen-group-header")).toBeTruthy();
    });

    const rows = screen
      .getAllByTestId(/^(unseen-group-header|date-header|session-item-)/)
      .map((el) => el.getAttribute("data-testid"));
    expect(rows.slice(0, 2)).toEqual([
      "unseen-group-header",
      "session-item-sess-unseen",
    ]);
    expect(rows.filter((row) => row === "session-item-sess-unseen")).toEqual([
      "session-item-sess-unseen",
    ]);
  });

  it("leads the list with a fixed group of unseen and running user chats", async () => {
    const unseenChat = { ...sessionA, id: "sess-unseen", sessionId: "s1" };
    const unseenCron = { ...sessionA, id: "sess-cron", source: "cron" };
    const unseenTalkTo = {
      ...sessionA,
      id: "sess-talk",
      sessionId: "default:to:helper:1:abc",
    };
    const unseenRunning = {
      ...sessionA,
      id: "sess-running",
      status: "running",
    };
    mockData([sessionA, unseenCron, unseenTalkTo, unseenRunning, unseenChat]);
    const groupsData = mockChatGroups();
    mockChatGroups.mockReturnValue({
      ...groupsData,
      groups: [
        ...groupsData.groups,
        { id: "cron", name: "Cron", order: 1, kind: "cron", pinned: false },
      ],
    });
    mockUnseenSessionIds.mockReturnValue(
      new Set(["sess-unseen", "sess-cron", "sess-talk", "sess-running"]),
    );
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("unseen-group-header")).toBeTruthy();
    });

    expect(screen.getByTestId("unseen-group-header").textContent).toBe("2");
    const rows = screen
      .getAllByTestId(/^(unseen-group-header|group-header-|session-item-)/)
      .map((el) => {
        const id = el.getAttribute("data-testid") ?? "";
        return id.startsWith("group-header-")
          ? `group:${id.slice("group-header-".length)}`
          : id;
      });
    // Only user chats move up; they are not listed again in their own group.
    expect(rows.slice(0, 4)).toEqual([
      "unseen-group-header",
      "session-item-sess-running",
      "session-item-sess-unseen",
      "group:default",
    ]);
    expect(rows.filter((row) => row === "session-item-sess-unseen")).toEqual([
      "session-item-sess-unseen",
    ]);
    expect(rows.filter((row) => row === "session-item-sess-running")).toEqual([
      "session-item-sess-running",
    ]);
    expect(rows).toContain("session-item-sess-talk");
    expect(rows).toContain("session-item-sess-cron");
  });

  it("puts the running chat on screen at the top of the unread group", async () => {
    const unseenChat = { ...sessionA, id: "sess-unseen" };
    const currentRunning = {
      ...sessionA,
      id: "sess-current",
      status: "running",
      generating: true,
    };
    mockData([sessionA, unseenChat, currentRunning]);
    mockUnseenSessionIds.mockReturnValue(new Set(["sess-unseen"]));
    renderWithProviders(<SidebarSessionList />, {
      initialEntries: ["/chat/sess-current"],
    });
    await waitFor(() => {
      expect(screen.getByTestId("unseen-group-header")).toBeTruthy();
    });

    expect(screen.getByTestId("unseen-group-header").textContent).toBe("2");
    const rows = screen
      .getAllByTestId(/^(unseen-group-header|group-header|session-item-)/)
      .map((el) => el.getAttribute("data-testid"));
    expect(rows.slice(0, 4)).toEqual([
      "unseen-group-header",
      "session-item-sess-current",
      "session-item-sess-unseen",
      "group-header-default",
    ]);
  });

  it("matches the running chat on screen by its backend id", async () => {
    const unseenChat = { ...sessionA, id: "sess-unseen" };
    const currentRunning = {
      ...sessionA,
      id: "1700000000000-abc123",
      realId: "uuid-current",
      status: "running",
      generating: true,
    };
    mockData([sessionA, unseenChat, currentRunning]);
    mockUnseenSessionIds.mockReturnValue(new Set(["sess-unseen"]));
    renderWithProviders(<SidebarSessionList />, {
      initialEntries: ["/chat/uuid-current"],
    });
    await waitFor(() => {
      expect(screen.getByTestId("unseen-group-header")).toBeTruthy();
    });

    const rows = screen
      .getAllByTestId(/^(unseen-group-header|session-item-)/)
      .map((el) => el.getAttribute("data-testid"));
    expect(rows.slice(0, 3)).toEqual([
      "unseen-group-header",
      "session-item-1700000000000-abc123",
      "session-item-sess-unseen",
    ]);
  });

  it("keeps an idle, already seen chat on screen in its own group", async () => {
    mockData([sessionA]);
    renderWithProviders(<SidebarSessionList />, {
      initialEntries: ["/chat/sess-a"],
    });
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });

    expect(screen.queryByTestId("unseen-group-header")).toBeNull();
  });

  it("marks finished unseen chats read and leaves running ones", async () => {
    useSessionAttentionStore.setState({ seenFinishedAt: {} });
    const done = {
      ...sessionA,
      id: "sess-done",
      lastFinishedAt: "2026-10-20T10:00:00.000Z",
    };
    const running = {
      ...sessionA,
      id: "sess-run",
      status: "running",
      generating: true,
      lastFinishedAt: "2026-10-19T10:00:00.000Z",
    };
    mockData([sessionA, done, running]);
    mockUnseenSessionIds.mockReturnValue(new Set(["sess-done", "sess-run"]));
    renderWithProviders(<SidebarSessionList />);

    fireEvent.click(
      await screen.findByRole("button", { name: "mark-all-seen" }),
    );
    expect(useSessionAttentionStore.getState().seenFinishedAt).toEqual({
      "agent-1:sess-done": "2026-10-20T10:00:00.000Z",
    });
  });

  it("offers no mark-all-read action while every unread chat is running", async () => {
    const running = {
      ...sessionA,
      id: "sess-run",
      status: "running",
      generating: true,
    };
    mockData([sessionA, running]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("unseen-group-header")).toBeTruthy();
    });

    expect(screen.queryByRole("button", { name: "mark-all-seen" })).toBeNull();
  });

  it("hides the unread group when nothing is unseen", async () => {
    mockData([sessionA]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    expect(screen.queryByTestId("unseen-group-header")).toBeNull();
  });

  it("routes session clicks through the injected callback", async () => {
    const onSessionClick = vi.fn();
    mockData([sessionA]);
    renderWithProviders(<SidebarSessionList onSessionClick={onSessionClick} />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("session-item-sess-a"));
    expect(onSessionClick).toHaveBeenCalledWith("sess-a");
  });

  it("dispatches a DOM event when no click handler is injected", async () => {
    const received: unknown[] = [];
    const listener = (e: Event) => received.push((e as CustomEvent).detail);
    window.addEventListener("qwenpaw:sidebar-select-session", listener);
    mockData([sessionA]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("session-item-sess-a"));
    expect(received).toEqual([{ sessionId: "sess-a" }]);
    window.removeEventListener("qwenpaw:sidebar-select-session", listener);
  });

  it("creates a new chat via the injected callback", async () => {
    const onNewChat = vi.fn();
    mockData([]);
    renderWithProviders(<SidebarSessionList onNewChat={onNewChat} />);
    fireEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(onNewChat).toHaveBeenCalled();
  });

  it("dispatches a new-chat DOM event when no handler is injected", async () => {
    let fired = false;
    const listener = () => {
      fired = true;
    };
    window.addEventListener("qwenpaw:sidebar-new-chat", listener);
    mockData([]);
    renderWithProviders(<SidebarSessionList />);
    fireEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(fired).toBe(true);
    window.removeEventListener("qwenpaw:sidebar-new-chat", listener);
  });

  it("collapses and expands the agent chats section", async () => {
    mockData([sessionA]);
    renderWithProviders(<SidebarSessionList />);
    const historyBtn = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.includes("Research Bot Chats"));
    expect(historyBtn).toBeTruthy();
    fireEvent.click(historyBtn!);
    // Collapsed: search input disappears
    await waitFor(() => {
      expect(screen.queryByTestId("virtual-list")).toBeNull();
    });
    // Expand again
    fireEvent.click(historyBtn!);
    await waitFor(() => {
      expect(screen.getByTestId("virtual-list")).toBeTruthy();
    });
  });

  it("filters sessions by the search query", async () => {
    mockData([sessionA, sessionB]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    const search = screen.getByPlaceholderText("Search…");
    fireEvent.change(search, { target: { value: "beta" } });
    await waitFor(() => {
      expect(screen.queryByTestId("session-item-sess-a")).toBeNull();
      expect(screen.getByTestId("session-item-sess-b")).toBeTruthy();
    });
  });

  it("opens the new-group input and creates a group on Enter", async () => {
    const createGroup = vi.fn().mockResolvedValue({ id: "g-new" });
    // group creation lives in source mode only
    localStorage.setItem("qwenpaw_session_group_mode", "source");
    mockData([sessionA]);
    mockChatGroups.mockReturnValue({
      groups: [
        {
          id: "default",
          name: "Uncategorized",
          order: 0,
          kind: "default",
          pinned: false,
        },
      ],
      createGroup,
      renameGroup: vi.fn(),
      pinGroup: vi.fn(),
      deleteGroup: vi.fn(),
      reorderGroups: vi.fn(),
    });
    renderWithProviders(<SidebarSessionList />);
    fireEvent.click(await screen.findByRole("button", { name: "New group" }));
    const input = screen.getByPlaceholderText("Group name");
    fireEvent.change(input, { target: { value: "My Group" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await waitFor(() => {
      expect(createGroup).toHaveBeenCalledWith("My Group");
    });
  });

  it("shows the loading spinner while the first load is in flight", async () => {
    mockData([], { loading: true });
    renderWithProviders(<SidebarSessionList />);
    // Loading state renders the Spin (no sessions yet)
    await waitFor(() => {
      expect(screen.queryByText("No conversations")).toBeNull();
    });
  });

  it("renders date sections in date mode", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "date");
    const older = {
      ...sessionA,
      id: "sess-old",
      name: "Older Chat",
      updatedAt: new Date(Date.now() - 40 * 86_400_000).toISOString(),
    };
    mockData([sessionA, older]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-old")).toBeTruthy();
    });
    // today + older buckets → two date headers, no group chrome
    expect(screen.getAllByTestId("date-header")).toHaveLength(2);
    expect(screen.queryByTestId("group-header-default")).toBeNull();
  });

  it("renders group sections in source mode", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "source");
    mockData([sessionA, sessionB]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("group-header-default")).toBeTruthy();
    });
    expect(screen.queryByTestId("date-header")).toBeNull();
    expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    expect(screen.getByTestId("session-item-sess-b")).toBeTruthy();
  });

  it("renders a flat list in none mode", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "none");
    mockData([sessionA, sessionB]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    expect(screen.queryByTestId("date-header")).toBeNull();
    expect(screen.queryByTestId("group-header-default")).toBeNull();
    expect(screen.getByTestId("session-item-sess-b")).toBeTruthy();
  });

  it("cycles grouping directly without opening a menu and persists it", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "date");
    mockData([sessionA, sessionB]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getAllByTestId("date-header").length).toBeGreaterThan(0);
    });

    expect(screen.queryByText("Search conversations")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "By time" }));
    expect(localStorage.getItem("qwenpaw_session_group_mode")).toBe("source");
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "By source" }));

    await waitFor(() => {
      expect(screen.queryByTestId("date-header")).toBeNull();
    });
    expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    expect(localStorage.getItem("qwenpaw_session_group_mode")).toBe("none");
    fireEvent.click(await screen.findByRole("button", { name: "No grouping" }));
    expect(localStorage.getItem("qwenpaw_session_group_mode")).toBe("date");
  });

  it("restores the chosen grouping mode after a remount", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "none");
    mockData([sessionA]);
    const { unmount } = renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    unmount();

    mockData([sessionA]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    expect(screen.queryByTestId("date-header")).toBeNull();
    expect(screen.queryByTestId("group-header-default")).toBeNull();
  });

  describe("virtual row heights", () => {
    function conversationFixture(count: number) {
      return Array.from({ length: count }, (_, index) => ({
        ...sessionA,
        id: `session-${index + 1}`,
        name: `Conversation ${index + 1}`,
        updatedAt: new Date(Date.now() - index * 1000).toISOString(),
      }));
    }

    it("allocates 38px session rows and 36px group headers", async () => {
      localStorage.setItem("qwenpaw_session_group_mode", "source");
      mockData(conversationFixture(12));
      renderWithProviders(<SidebarSessionList />);
      await waitFor(() => {
        expect(screen.getByTestId("virtual-list")).toBeTruthy();
      });
      const list = mockListProps.current;
      expect(list).toBeTruthy();
      // rows: groupHeader(default, 12) followed by all 12 sessions;
      // session pitch includes the 2px row margin
      expect(list!.itemSize(0)).toBe(36);
      expect(list!.itemSize(1)).toBe(38);
      expect(list!.itemSize(11)).toBe(38);
    });

    it("allocates 36px date headers in date mode", async () => {
      localStorage.setItem("qwenpaw_session_group_mode", "date");
      mockData([sessionA]);
      renderWithProviders(<SidebarSessionList />);
      await waitFor(() => {
        expect(screen.getByTestId("virtual-list")).toBeTruthy();
      });
      const list = mockListProps.current!;
      // rows: dateHeader(today), session — headers share the group
      // header row height now that both are collapsible chips
      expect(list.itemSize(0)).toBe(36);
      expect(list.itemSize(1)).toBe(38);
    });

    it.each(["cron", "subagents"] as const)(
      "reserves the %s header's source spacing before a conversation",
      async (kind) => {
        localStorage.setItem("qwenpaw_session_group_mode", "source");
        mockData([{ ...sessionA, groupId: kind }]);
        mockChatGroups.mockReturnValue({
          groups: [
            {
              id: "default",
              name: "Uncategorized",
              kind: "default",
              order: 0,
              pinned: false,
            },
            { id: kind, name: kind, kind, order: 1, pinned: false },
          ],
          createGroup: vi.fn(),
          renameGroup: vi.fn(),
          pinGroup: vi.fn(),
          deleteGroup: vi.fn(),
          reorderGroups: vi.fn(),
        });
        renderWithProviders(<SidebarSessionList />);
        await waitFor(() => {
          expect(screen.getByTestId(`group-header-${kind}`)).toBeTruthy();
          expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
        });
        const list = mockListProps.current!;
        // The source chip is 32px high with 8px top / 2px bottom margins.
        expect(list.itemSize(0)).toBe(36);
        expect(list.itemSize(1)).toBe(42);
        expect(list.itemSize(2)).toBe(38);
      },
    );
  });

  it("shows empty groups in source mode", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "source");
    mockData([sessionA]);
    mockChatGroups.mockReturnValue({
      groups: [
        {
          id: "default",
          name: "Uncategorized",
          order: 0,
          kind: "default",
          pinned: false,
        },
        {
          id: "cron",
          name: "Scheduled tasks",
          order: 1,
          kind: "cron",
          pinned: false,
        },
      ],
      createGroup: vi.fn().mockResolvedValue({ id: "g-new" }),
      renameGroup: vi.fn(),
      pinGroup: vi.fn(),
      deleteGroup: vi.fn(),
      reorderGroups: vi.fn(),
    });
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("group-header-default")).toBeTruthy();
    });
    // empty groups stay visible: they are drop targets and move targets
    expect(screen.getByTestId("group-header-cron")).toBeTruthy();
    expect(mockListProps.current!.itemCount).toBe(3);
  });

  it("offers group creation only in source mode", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "date");
    mockData([sessionA]);
    const first = renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });

    // date mode: no New group entry
    expect(screen.queryByRole("button", { name: "New group" })).toBeNull();
    first.unmount();

    // source mode: the entry appears
    localStorage.setItem("qwenpaw_session_group_mode", "source");
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    });
    expect(
      await screen.findByRole("button", { name: "New group" }),
    ).toBeTruthy();
  });

  it("renders three date tiers and skips empty ones", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "date");
    const weekSession = {
      ...sessionA,
      id: "sess-week",
      name: "Week Chat",
      updatedAt: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    };
    const oldSession = {
      ...sessionA,
      id: "sess-old",
      name: "Older Chat",
      updatedAt: new Date(Date.now() - 40 * 86_400_000).toISOString(),
    };
    // today + week present, month empty (must not render)
    mockData([sessionA, weekSession, oldSession]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getAllByTestId("date-header")).toHaveLength(3);
    });
    // the test i18n mock renders keys as labels; header text also
    // carries the count, so match by prefix
    expect(screen.getByText(/chat\.group\.today/)).toBeTruthy();
    expect(screen.getByText(/chat\.group\.week/)).toBeTruthy();
    expect(screen.getByText(/chat\.group\.older/)).toBeTruthy();
    expect(screen.queryByText(/chat\.group\.month/)).toBeNull();
  });

  it("floats pinned conversations to the top of their date tier", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "date");
    const pinnedOld = {
      ...sessionA,
      id: "sess-pinned-old",
      name: "Pinned Old",
      pinned: true,
      updatedAt: new Date(Date.now() - 40 * 86_400_000).toISOString(),
    };
    const plainOld = {
      ...sessionA,
      id: "sess-old",
      name: "Plain Old",
      updatedAt: new Date(Date.now() - 41 * 86_400_000).toISOString(),
    };
    mockData([pinnedOld, plainOld]);
    renderWithProviders(<SidebarSessionList />);
    await waitFor(() => {
      expect(screen.getAllByTestId("date-header")).toHaveLength(1);
    });
    const rows = Array.from(
      document.querySelectorAll("[data-testid^='session-item-']"),
    ).map((node) => node.getAttribute("data-testid"));
    expect(rows).toEqual([
      "session-item-sess-pinned-old",
      "session-item-sess-old",
    ]);
  });

  it("folds and unfolds date sections like group sections", async () => {
    localStorage.setItem("qwenpaw_session_group_mode", "date");
    const older = {
      ...sessionA,
      id: "sess-old",
      name: "Older Chat",
      updatedAt: new Date(Date.now() - 40 * 86_400_000).toISOString(),
    };
    mockData([sessionA, older]);
    renderWithProviders(<SidebarSessionList />);

    // two date sections, both expanded
    await waitFor(() => {
      expect(screen.getAllByTestId("date-header")).toHaveLength(2);
    });
    expect(screen.getByTestId("session-item-sess-old")).toBeTruthy();

    const headers = screen.getAllByTestId("date-header");
    expect(headers[0]).toHaveAttribute("aria-expanded", "true");

    // fold the "older" section (second header)
    fireEvent.click(headers[1]);
    await waitFor(() => {
      expect(screen.queryByTestId("session-item-sess-old")).toBeNull();
    });
    expect(screen.getByTestId("session-item-sess-a")).toBeTruthy();
    expect(screen.getAllByTestId("date-header")[1]).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    // unfold again
    fireEvent.click(screen.getAllByTestId("date-header")[1]);
    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-old")).toBeTruthy();
    });
  });
});
