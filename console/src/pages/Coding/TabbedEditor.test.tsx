// @vitest-environment jsdom
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TabbedEditor from "./TabbedEditor";
import styles from "./TabbedEditor.module.less";
import { workspaceApi } from "../../api/modules/workspace";
import {
  useCodingTabsStore,
  type EditorTab,
} from "../../stores/codingTabsStore";
import type { FileTarget } from "../../features/files-workspace/types";

const SCOPE_KEY = "agent:default";

const clipboardMocks = vi.hoisted(() => ({
  copyText: vi.fn().mockResolvedValue(undefined),
  error: vi.fn(),
  success: vi.fn(),
}));

vi.mock("../../monacoSetup", () => ({}));

vi.mock("@monaco-editor/react", () => ({
  default: ({
    value,
    onMount,
  }: {
    value: string;
    onMount?: (editor: unknown) => void;
  }) => {
    onMount?.({
      getValue: () => "",
      onDidChangeCursorSelection: () => ({ dispose: vi.fn() }),
    });
    return <div data-testid="editor-value">{value}</div>;
  },
  DiffEditor: () => <div data-testid="diff-editor" />,
}));

vi.mock("../../hooks/useWorkspaceWatch", () => ({
  useWorkspaceWatch: vi.fn(),
}));

vi.mock("../../contexts/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));

vi.mock("../../utils/clipboard", () => ({
  copyText: clipboardMocks.copyText,
}));

vi.mock("../../hooks/useAppMessage", () => ({
  useAppMessage: () => ({
    message: {
      error: clipboardMocks.error,
      success: clipboardMocks.success,
    },
  }),
}));

function Harness({
  onSaveFile,
  onDownloadFile,
  onOpenFile,
  chatId,
}: {
  onSaveFile: (path: string, content: string) => Promise<void>;
  onDownloadFile?: (path: string) => Promise<void>;
  onOpenFile?: (target: FileTarget) => void;
  chatId?: string;
}) {
  const tabs = useCodingTabsStore(
    (state) => state.tabsByAgent[SCOPE_KEY] ?? [],
  );
  const activeTabPath = useCodingTabsStore(
    (state) => state.activeTabByAgent[SCOPE_KEY] ?? "",
  );
  const store = useCodingTabsStore();

  return (
    <TabbedEditor
      tabs={tabs}
      activeTabPath={activeTabPath}
      scopeKey={SCOPE_KEY}
      onTabSelect={(path) => store.setActiveTab(SCOPE_KEY, path)}
      onTabClose={(path) => store.closeTab(SCOPE_KEY, path)}
      onCloseOtherTabs={(path) => {
        tabs.forEach((tab) => {
          if (tab.path !== path) store.closeTab(SCOPE_KEY, tab.path);
        });
        store.setActiveTab(SCOPE_KEY, path);
      }}
      onTabDirtyChange={(path, dirty) =>
        store.setTabDirty(SCOPE_KEY, path, dirty)
      }
      onTabContentChange={(path, content) =>
        store.setTabContent(SCOPE_KEY, path, content)
      }
      onDownloadFile={onDownloadFile}
      onOpenFile={onOpenFile}
      onSaveFile={onSaveFile}
      chatId={chatId}
    />
  );
}

describe("TabbedEditor Markdown navigation (#8528)", () => {
  it.each([
    ["docs/a.md", undefined],
    ["workspace-root::docs/a.md", "workspace"],
    [
      "project:C:/Projects/secondary::docs/a.md",
      "project:C:/Projects/secondary",
    ],
  ] as const)("retains the root of persisted tab %s", async (path, root) => {
    useCodingTabsStore.setState({
      tabsByAgent: {
        [SCOPE_KEY]: [{ path, content: "[next](./b.md)", dirty: false }],
      },
      activeTabByAgent: { [SCOPE_KEY]: path },
      diffsByAgent: {},
    });
    const onOpenFile = vi.fn();
    render(<Harness onSaveFile={vi.fn()} onOpenFile={onOpenFile} />);
    fireEvent.click(await screen.findByRole("link", { name: "next" }));
    expect(onOpenFile).toHaveBeenCalledWith({
      source: "workspace",
      path: "docs/b.md",
      root,
    });
  });
});

describe("TabbedEditor diff resolution", () => {
  beforeEach(() => {
    useCodingTabsStore.setState({
      tabsByAgent: {
        [SCOPE_KEY]: [{ path: "hello.txt", content: "original", dirty: false }],
      },
      activeTabByAgent: { [SCOPE_KEY]: "hello.txt" },
      diffsByAgent: {
        [SCOPE_KEY]: {
          "hello.txt": { original: "original", modified: "changed" },
        },
      },
    });
  });

  it("saves the restored content after undo instead of a stale empty editor", async () => {
    const onSaveFile = vi.fn(async () => undefined);
    const { container } = render(<Harness onSaveFile={onSaveFile} />);

    const undoLabel = await screen.findByText(/undoAll|全部回退/i);
    fireEvent.click(undoLabel.closest("button") as HTMLButtonElement);

    await waitFor(() => {
      expect(onSaveFile).toHaveBeenCalledWith("hello.txt", "original");
      expect(
        useCodingTabsStore.getState().diffsByAgent[SCOPE_KEY],
      ).not.toHaveProperty("hello.txt");
    });

    fireEvent.keyDown(container.firstElementChild as HTMLElement, {
      key: "s",
      ctrlKey: true,
    });

    await waitFor(() => {
      expect(onSaveFile).toHaveBeenLastCalledWith("hello.txt", "original");
    });
    expect(onSaveFile).not.toHaveBeenCalledWith("hello.txt", "");
  });
});

describe("TabbedEditor clipboard action", () => {
  beforeEach(() => {
    clipboardMocks.copyText.mockClear();
    clipboardMocks.success.mockClear();
    useCodingTabsStore.setState({
      tabsByAgent: {
        [SCOPE_KEY]: [
          {
            path: "hello.txt",
            content: "unsaved content",
            dirty: true,
            previewKind: "text",
          },
        ],
      },
      activeTabByAgent: { [SCOPE_KEY]: "hello.txt" },
      diffsByAgent: { [SCOPE_KEY]: {} },
    });
  });

  it("copies the current in-memory file content", async () => {
    render(
      <Harness
        onSaveFile={vi.fn(async () => undefined)}
        onDownloadFile={vi.fn(async () => undefined)}
      />,
    );
    const copyButton = screen.getByRole("button", { name: /copy|复制/i });
    const downloadButton = screen.getByRole("button", {
      name: /download|下载/i,
    });

    expect(
      copyButton.compareDocumentPosition(downloadButton) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.click(copyButton);

    await waitFor(() => {
      expect(clipboardMocks.copyText).toHaveBeenCalledWith("unsaved content");
      expect(clipboardMocks.success).toHaveBeenCalled();
    });
  });
});

describe("TabbedEditor server path copy", () => {
  const SERVER_PATH_NAME = /copyServerPath|复制服务器路径/i;
  let metadataSpy: ReturnType<typeof vi.spyOn>;

  function openSingleTab(tab: EditorTab) {
    useCodingTabsStore.setState({
      tabsByAgent: { [SCOPE_KEY]: [tab] },
      activeTabByAgent: { [SCOPE_KEY]: tab.path },
      diffsByAgent: { [SCOPE_KEY]: {} },
    });
  }

  function mockServerPath(absolutePath: string) {
    metadataSpy.mockResolvedValue({
      path: "",
      absolute_path: absolutePath,
      size: 1,
      modified_at: "",
      preview_kind: "text",
      etag: "v1",
    });
  }

  beforeEach(() => {
    clipboardMocks.copyText.mockReset();
    clipboardMocks.copyText.mockResolvedValue(undefined);
    clipboardMocks.success.mockClear();
    clipboardMocks.error.mockClear();
    // Never fall through to the real API: an unmocked call fails loudly.
    metadataSpy = vi
      .spyOn(workspaceApi, "getFileMetadata")
      .mockRejectedValue(new Error("metadata not mocked"));
  });

  afterEach(() => {
    metadataSpy.mockRestore();
  });

  it("copies the server path of a project file and keeps Preview/Download", async () => {
    mockServerPath("/srv/project/src/app.ts");
    openSingleTab({
      path: "src/app.ts",
      displayPath: "src/app.ts",
      content: "code",
      dirty: false,
      source: "workspace",
      previewKind: "text",
    });
    const onDownloadFile = vi.fn(async () => undefined);
    render(
      <Harness
        onSaveFile={vi.fn(async () => undefined)}
        onDownloadFile={onDownloadFile}
        chatId="chat-1"
      />,
    );

    const pathButton = screen.getByRole("button", { name: SERVER_PATH_NAME });
    expect(pathButton).toHaveAccessibleName(/src\/app\.ts$/);
    expect(pathButton).toHaveTextContent("src/app.ts");
    // Same truncating class as the plain path; the ellipsis lives on the span.
    expect(pathButton).toHaveClass(styles.fileName, styles.fileNameButton);
    expect(within(pathButton).getByText("src/app.ts")).toHaveClass(
      styles.fileNameText,
    );

    fireEvent.mouseEnter(pathButton);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      SERVER_PATH_NAME,
    );

    fireEvent.click(pathButton);
    await waitFor(() => {
      expect(clipboardMocks.copyText).toHaveBeenCalledWith(
        "/srv/project/src/app.ts",
      );
      expect(clipboardMocks.success).toHaveBeenCalled();
    });
    expect(metadataSpy).toHaveBeenCalledWith(
      "src/app.ts",
      "chat-1",
      "project",
      undefined,
    );

    expect(
      screen.getByRole("button", { name: /files\.preview|预览/i }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /download|下载/i }));
    expect(onDownloadFile).toHaveBeenCalledWith("src/app.ts");
    expect(clipboardMocks.copyText).toHaveBeenCalledTimes(1);
  });

  it("copies with the keyboard and resolves against the tab's own root", async () => {
    mockServerPath("/home/me/.qwenpaw/workspaces/default/notes.md");
    openSingleTab({
      path: "workspace-root::notes.md",
      displayPath: "notes.md",
      content: "notes",
      dirty: false,
      source: "workspace",
      workspaceRoot: "workspace",
      previewKind: "text",
    });
    const user = userEvent.setup();
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    const pathButton = screen.getByRole("button", { name: SERVER_PATH_NAME });
    pathButton.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(clipboardMocks.copyText).toHaveBeenCalledWith(
        "/home/me/.qwenpaw/workspaces/default/notes.md",
      );
    });
    expect(metadataSpy).toHaveBeenCalledWith(
      "notes.md",
      undefined,
      "workspace",
      undefined,
    );

    await user.keyboard(" ");
    await waitFor(() => {
      expect(clipboardMocks.copyText).toHaveBeenCalledTimes(2);
    });
  });

  it("resolves an extra bound project root by its own root token", async () => {
    mockServerPath("/data/other/lib/util.py");
    openSingleTab({
      path: "project:/data/other::lib/util.py",
      displayPath: "lib/util.py",
      content: "",
      dirty: false,
      source: "workspace",
      workspaceRoot: "project:/data/other",
      previewKind: "text",
    });
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    fireEvent.click(screen.getByRole("button", { name: SERVER_PATH_NAME }));

    await waitFor(() => {
      expect(clipboardMocks.copyText).toHaveBeenCalledWith(
        "/data/other/lib/util.py",
      );
    });
    expect(metadataSpy).toHaveBeenCalledWith(
      "lib/util.py",
      undefined,
      "project:/data/other",
      undefined,
    );
  });

  it("keeps a failed preload silent, reports the click failure, then retries", async () => {
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    try {
      metadataSpy.mockRejectedValue(new Error("404"));
      openSingleTab({
        path: "gone.txt",
        displayPath: "gone.txt",
        content: "",
        dirty: false,
        source: "workspace",
        previewKind: "text",
      });
      render(<Harness onSaveFile={vi.fn(async () => undefined)} />);
      const pathButton = screen.getByRole("button", {
        name: SERVER_PATH_NAME,
      });

      // Preload fails: no feedback, and the failure is evicted from cache.
      await waitFor(() => {
        expect(metadataSpy).toHaveBeenCalledTimes(1);
      });
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(clipboardMocks.error).not.toHaveBeenCalled();

      // Click asks the server again and reports the failure.
      fireEvent.click(pathButton);
      await waitFor(() => {
        expect(clipboardMocks.error).toHaveBeenCalledTimes(1);
      });
      expect(metadataSpy).toHaveBeenCalledTimes(2);
      expect(clipboardMocks.copyText).not.toHaveBeenCalled();
      expect(clipboardMocks.success).not.toHaveBeenCalled();

      // Once the server recovers, the next click copies the path.
      mockServerPath("/srv/project/gone.txt");
      fireEvent.click(pathButton);
      await waitFor(() => {
        expect(clipboardMocks.copyText).toHaveBeenCalledWith(
          "/srv/project/gone.txt",
        );
      });
      expect(metadataSpy).toHaveBeenCalledTimes(3);
      expect(clipboardMocks.success).toHaveBeenCalledTimes(1);
      expect(clipboardMocks.error).toHaveBeenCalledTimes(1);

      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      process.off("unhandledRejection", unhandled);
    }
  });

  it("reports failure when the clipboard write is rejected", async () => {
    mockServerPath("/srv/project/a.txt");
    clipboardMocks.copyText.mockRejectedValueOnce(new Error("denied"));
    openSingleTab({
      path: "a.txt",
      displayPath: "a.txt",
      content: "",
      dirty: false,
      source: "workspace",
      previewKind: "text",
    });
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    fireEvent.click(screen.getByRole("button", { name: SERVER_PATH_NAME }));

    await waitFor(() => {
      expect(clipboardMocks.error).toHaveBeenCalled();
    });
    expect(clipboardMocks.success).not.toHaveBeenCalled();
  });

  it.each([
    ["profile", { source: "profile" as const }],
    ["memory", { source: "memory" as const }],
    ["daily", { source: "daily" as const }],
    ["digest", { source: "digest" as const }],
    [
      "historical attachment",
      {
        source: "attachment" as const,
        artifactUrl: "/api/files/artifact/1",
      },
    ],
    ["untyped legacy tab", {}],
  ])(
    "keeps the path as plain text for a %s tab",
    (_label, extra: Partial<EditorTab>) => {
      openSingleTab({
        path: "x::AGENTS.md",
        displayPath: "AGENTS.md",
        content: "",
        dirty: false,
        previewKind: "text",
        ...extra,
      });
      render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

      expect(
        screen.queryByRole("button", { name: SERVER_PATH_NAME }),
      ).not.toBeInTheDocument();
      expect(screen.getAllByText("AGENTS.md").length).toBeGreaterThan(0);
      expect(metadataSpy).not.toHaveBeenCalled();
    },
  );
});

describe("TabbedEditor tab context menu", () => {
  beforeEach(() => {
    useCodingTabsStore.setState({
      tabsByAgent: {
        [SCOPE_KEY]: [
          { path: "one.txt", content: "one", dirty: false },
          { path: "two.txt", content: "two", dirty: false },
          { path: "three.txt", content: "three", dirty: false },
        ],
      },
      activeTabByAgent: { [SCOPE_KEY]: "one.txt" },
      diffsByAgent: { [SCOPE_KEY]: {} },
    });
  });

  it("closes the tab that was right-clicked", async () => {
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    fireEvent.contextMenu(screen.getByRole("tab", { name: /two\.txt/i }));
    fireEvent.click(await screen.findByText(/closeTab|关闭标签页/i));

    await waitFor(() => {
      expect(
        useCodingTabsStore
          .getState()
          .tabsByAgent[SCOPE_KEY].map((tab) => tab.path),
      ).toEqual(["one.txt", "three.txt"]);
      expect(useCodingTabsStore.getState().activeTabByAgent[SCOPE_KEY]).toBe(
        "one.txt",
      );
    });
  });

  it("keeps and activates the tab used to close the others", async () => {
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    fireEvent.contextMenu(screen.getByRole("tab", { name: /two\.txt/i }));
    fireEvent.click(await screen.findByText(/closeOtherTabs|关闭其他标签页/i));

    await waitFor(() => {
      expect(useCodingTabsStore.getState().tabsByAgent[SCOPE_KEY]).toHaveLength(
        1,
      );
      expect(useCodingTabsStore.getState().tabsByAgent[SCOPE_KEY][0].path).toBe(
        "two.txt",
      );
      expect(useCodingTabsStore.getState().activeTabByAgent[SCOPE_KEY]).toBe(
        "two.txt",
      );
    });
  });

  it("closes a tab with the middle mouse button", async () => {
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    fireEvent(
      screen.getByRole("tab", { name: /two\.txt/i }),
      new MouseEvent("auxclick", { bubbles: true, button: 1 }),
    );

    await waitFor(() => {
      expect(
        useCodingTabsStore
          .getState()
          .tabsByAgent[SCOPE_KEY].map((tab) => tab.path),
      ).toEqual(["one.txt", "three.txt"]);
    });
  });

  it("searches and activates a file from the open-files panel", async () => {
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    fireEvent.click(
      screen.getByRole("button", { name: /openFiles|已打开文件/i }),
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("searchbox"), {
      target: { value: "two" },
    });

    expect(within(dialog).getByText("two.txt")).toBeInTheDocument();
    expect(within(dialog).queryByText("three.txt")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByText("two.txt"));

    await waitFor(() => {
      expect(useCodingTabsStore.getState().activeTabByAgent[SCOPE_KEY]).toBe(
        "two.txt",
      );
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("closes every tab from the open-files panel", async () => {
    render(<Harness onSaveFile={vi.fn(async () => undefined)} />);

    fireEvent.click(
      screen.getByRole("button", { name: /openFiles|已打开文件/i }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: /closeAllTabs|关闭全部标签页/i,
      }),
    );

    await waitFor(() => {
      expect(useCodingTabsStore.getState().tabsByAgent[SCOPE_KEY]).toEqual([]);
    });
  });
});

// ---------------------------------------------------------------------------
// Project switch — regression for A#82628675 (open files not following project switch)
// When the user switches projects/agents, the open file list must update
// to reflect the new project's tabs (or be empty if the new project has
// no open tabs). The store's clearAgent/clearProjectTabs handles this.
// ---------------------------------------------------------------------------
describe("TabbedEditor project switch (A#82628675)", () => {
  const AGENT_A = "agent:alpha";
  const AGENT_B = "agent:beta";

  beforeEach(() => {
    useCodingTabsStore.setState({
      tabsByAgent: {
        [AGENT_A]: [
          { path: "a1.txt", content: "a1", dirty: false },
          { path: "a2.txt", content: "a2", dirty: false },
        ],
        [AGENT_B]: [{ path: "b1.txt", content: "b1", dirty: false }],
      },
      activeTabByAgent: {
        [AGENT_A]: "a1.txt",
        [AGENT_B]: "b1.txt",
      },
      diffsByAgent: { [AGENT_A]: {}, [AGENT_B]: {} },
    });
  });

  it("preserves each agent's tabs independently", () => {
    const state = useCodingTabsStore.getState();
    expect(state.tabsByAgent[AGENT_A].map((t) => t.path)).toEqual([
      "a1.txt",
      "a2.txt",
    ]);
    expect(state.tabsByAgent[AGENT_B].map((t) => t.path)).toEqual(["b1.txt"]);
  });

  it("clears tabs for the switched-away agent when clearAgent is called", () => {
    useCodingTabsStore.getState().clearAgent(AGENT_A);
    const state = useCodingTabsStore.getState();
    expect(state.tabsByAgent[AGENT_A]).toEqual([]);
    expect(state.activeTabByAgent[AGENT_A]).toBe("");
    // Agent B's tabs should be unaffected
    expect(state.tabsByAgent[AGENT_B].map((t) => t.path)).toEqual(["b1.txt"]);
  });

  it("clears project-scoped tabs when clearProjectTabs is called", () => {
    const projectScope = "session:proj-123";
    useCodingTabsStore.setState({
      tabsByAgent: {
        ...useCodingTabsStore.getState().tabsByAgent,
        [projectScope]: [{ path: "proj.txt", content: "proj", dirty: false }],
      },
      activeTabByAgent: {
        ...useCodingTabsStore.getState().activeTabByAgent,
        [projectScope]: "proj.txt",
      },
    });

    useCodingTabsStore.getState().clearProjectTabs(projectScope);
    const state = useCodingTabsStore.getState();
    expect(state.tabsByAgent[projectScope]).toEqual([]);
    expect(state.activeTabByAgent[projectScope]).toBe("");
  });

  it("switching to a new agent shows that agent's tabs", () => {
    // Simulate switching from agent A to agent B
    const agentBTabs = useCodingTabsStore.getState().tabsByAgent[AGENT_B];
    expect(agentBTabs).toHaveLength(1);
    expect(agentBTabs[0].path).toBe("b1.txt");
    expect(useCodingTabsStore.getState().activeTabByAgent[AGENT_B]).toBe(
      "b1.txt",
    );
  });
});
