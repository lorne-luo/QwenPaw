import { render, screen, within, waitFor, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import FilePreview, { getPreviewType, isPreviewable } from "./FilePreview";

describe("FilePreview", () => {
  it("keeps math code blocks in the renderable code block controls", () => {
    render(
      <FilePreview
        filePath="formula.md"
        content={["```math", "x^2 + y^2 = z^2", "```"].join("\n")}
      />,
    );

    const tabs = screen.getAllByRole("tab", { hidden: true });
    expect(tabs).toHaveLength(2);
    expect(screen.getByLabelText("common.copy")).toBeInTheDocument();
    expect(screen.getByLabelText("common.download")).toBeInTheDocument();

    (tabs[1] as HTMLButtonElement).click();
    expect(screen.getByRole("tabpanel", { hidden: true })).toHaveTextContent(
      "x^2 + y^2 = z^2",
    );
  });

  it("shows YAML frontmatter as metadata while preserving the body", () => {
    render(
      <FilePreview
        filePath="memory-search.md"
        content={[
          "---",
          "description: Memory Search query guidance",
          "name: memory-search-query-best-practices",
          "---",
          "",
          "## When to Use",
          "",
          "Use this when searching memory.",
        ].join("\n")}
      />,
    );

    const frontmatter = within(screen.getByLabelText("Front matter"));
    expect(frontmatter.getByText("description")).toBeInTheDocument();
    expect(
      frontmatter.getByText("Memory Search query guidance"),
    ).toBeInTheDocument();
    expect(frontmatter.getByText("name")).toBeInTheDocument();
    expect(
      frontmatter.getByText("memory-search-query-best-practices"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "When to Use" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Use this when searching memory."),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// getPreviewType / isPreviewable — regression for #5863
// (Coding session images were not displayed because image files did not
// enable preview mode; the file-type decision must be covered by tests)
// ---------------------------------------------------------------------------
describe("getPreviewType (#5863)", () => {
  it.each([
    ["photo.png", "image"],
    ["photo.jpg", "image"],
    ["photo.JPEG", "image"], // case-insensitive extension
    ["anim.gif", "image"],
    ["pic.webp", "image"],
    ["icon.svg", "image"],
    ["favicon.ico", "image"],
    ["bitmap.bmp", "image"],
  ])("detects image type for %s", (path, expected) => {
    expect(getPreviewType(path)).toBe(expected);
  });

  it.each([
    ["clip.mp4", "video"],
    ["clip.WEBM", "video"],
    ["clip.mov", "video"],
    ["clip.m4v", "video"],
    ["clip.ogv", "video"],
    ["voice.mp3", "audio"],
    ["voice.wav", "audio"],
    ["voice.ogg", "audio"],
    ["voice.oga", "audio"],
    ["voice.m4a", "audio"],
    ["voice.aac", "audio"],
    ["voice.flac", "audio"],
    ["voice.opus", "audio"],
  ])("detects media type for %s", (path, expected) => {
    expect(getPreviewType(path)).toBe(expected);
  });

  it("does not preview media formats browsers cannot play", () => {
    expect(getPreviewType("movie.mkv")).toBe("none");
    expect(getPreviewType("movie.avi")).toBe("none");
    expect(getPreviewType("song.wma")).toBe("none");
  });

  it("detects pdf / markdown / html / csv types", () => {
    expect(getPreviewType("doc.pdf")).toBe("pdf");
    expect(getPreviewType("README.md")).toBe("markdown");
    expect(getPreviewType("notes.mdx")).toBe("markdown");
    expect(getPreviewType("page.html")).toBe("html");
    expect(getPreviewType("page.htm")).toBe("html");
    expect(getPreviewType("data.csv")).toBe("csv");
  });

  it("returns none for unknown or extensionless paths", () => {
    expect(getPreviewType("script.py")).toBe("none");
    expect(getPreviewType("archive.zip")).toBe("none");
    expect(getPreviewType("Makefile")).toBe("none");
  });

  it("uses only the last extension segment", () => {
    // "notes.md.bak" must NOT be treated as markdown
    expect(getPreviewType("notes.md.bak")).toBe("none");
    expect(getPreviewType("photo.png.tmp")).toBe("none");
  });
});

describe("isPreviewable (#5863)", () => {
  it("returns true for previewable types and false for others", () => {
    expect(isPreviewable("photo.png")).toBe(true);
    expect(isPreviewable("README.md")).toBe(true);
    expect(isPreviewable("script.py")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Image preview rendering — regression for A#82584296 (image preview not working)
// When a file has an image extension, FilePreview must render an <img>
// element (after blob loading) rather than falling through to null.
// ---------------------------------------------------------------------------

// Mock dependencies for image preview tests
vi.mock("@/stores/agentStore", () => ({
  useAgentStore: vi.fn((selector?: (s: any) => any) =>
    selector
      ? selector({ selectedAgent: "default" })
      : { selectedAgent: "default" },
  ),
}));
vi.mock("@/api/authHeaders", () => ({
  buildAuthHeaders: () => ({}),
}));
vi.mock("@/api/modules/workspace", () => ({
  workspaceApi: {
    getFileDownloadUrl: (path: string) => `/api/files/${path}`,
    loadFileChunk: vi.fn(),
  },
}));

describe("FilePreview image rendering (A#82584296)", () => {
  const mockBlobUrl = "blob:http://localhost/fake-blob-id";
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      blob: () =>
        Promise.resolve(new Blob(["fake-image-data"], { type: "image/png" })),
    });
    global.fetch = fetchSpy as unknown as typeof fetch;
    vi.spyOn(URL, "createObjectURL").mockReturnValue(mockBlobUrl);
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  });

  it("renders an <img> element for PNG files after loading", async () => {
    await act(async () => {
      render(<FilePreview filePath="screenshot.png" content="" />);
    });

    await waitFor(() => {
      const img = screen.getByRole("img");
      expect(img).toBeInTheDocument();
      expect(img.getAttribute("src")).toBe(mockBlobUrl);
    });
  });

  it("sets alt text from the filename", async () => {
    await act(async () => {
      render(<FilePreview filePath="photos/vacation.jpg" content="" />);
    });

    await waitFor(() => {
      const img = screen.getByRole("img");
      expect(img.getAttribute("alt")).toBe("vacation.jpg");
    });
  });

  it("shows error state when blob fetch fails", async () => {
    fetchSpy.mockResolvedValue({
      ok: false,
      status: 404,
    });

    await act(async () => {
      render(<FilePreview filePath="missing.png" content="" />);
    });

    // After fetch fails, should not render an <img>
    await waitFor(() => {
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
    });
  });
});

describe("FilePreview media rendering", () => {
  const mockBlobUrl = "blob:http://localhost/fake-media-id";

  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      blob: () =>
        Promise.resolve(new Blob(["fake-media"], { type: "video/mp4" })),
    }) as unknown as typeof fetch;
    vi.spyOn(URL, "createObjectURL").mockReturnValue(mockBlobUrl);
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  });

  it("plays a video file through the authenticated blob URL", async () => {
    let container!: HTMLElement;
    await act(async () => {
      ({ container } = render(
        <FilePreview filePath="out/clip.mp4" content="" />,
      ));
    });

    await waitFor(() => {
      const video = container.querySelector("video");
      expect(video).not.toBeNull();
      expect(video?.getAttribute("src")).toBe(mockBlobUrl);
      expect(video?.hasAttribute("controls")).toBe(true);
    });
  });

  it("plays an audio file through the authenticated blob URL", async () => {
    let container!: HTMLElement;
    await act(async () => {
      ({ container } = render(
        <FilePreview filePath="out/voice.mp3" content="" />,
      ));
    });

    await waitFor(() => {
      const audio = container.querySelector("audio");
      expect(audio).not.toBeNull();
      expect(audio?.getAttribute("src")).toBe(mockBlobUrl);
      expect(audio?.hasAttribute("controls")).toBe(true);
    });
  });

  it("shows the load-failed state instead of a player", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 404 }) as unknown as typeof fetch;
    let container!: HTMLElement;
    await act(async () => {
      ({ container } = render(
        <FilePreview filePath="missing.mp4" content="" />,
      ));
    });

    await waitFor(() => {
      expect(screen.getByText("files.loadFailed")).toBeInTheDocument();
    });
    expect(container.querySelector("video")).toBeNull();
  });
});
