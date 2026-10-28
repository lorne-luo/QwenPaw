import { describe, expect, it } from "vitest";
import {
  parseAbsoluteFileLink,
  parseInternalFileLink,
  filePathFromPreviewUrl,
  rootForFileReference,
  toProjectRelativePath,
} from "./internalFileLinks";

describe("parseInternalFileLink", () => {
  it("parses relative paths and line locations", () => {
    expect(parseInternalFileLink("src/app.py#L120C4")).toEqual({
      source: "workspace",
      path: "src/app.py",
      line: 120,
      column: 4,
    });
  });

  it.each([
    "https://example.com/file.py",
    "mailto:team@example.com",
    "/etc/passwd",
    "../secret",
    "src/../secret",
    "C:/secret",
    "\\\\server\\share",
  ])("leaves unsafe or external target untouched: %s", (href) => {
    expect(parseInternalFileLink(href)).toBeNull();
  });

  it("maps absolute project files to safe relative paths", () => {
    expect(
      toProjectRelativePath(
        "/Users/demo/project/src/app.ts",
        "/Users/demo/project",
      ),
    ).toBe("src/app.ts");
    expect(
      toProjectRelativePath(
        "C:\\Work\\Project\\src\\app.ts",
        "c:\\work\\project",
      ),
    ).toBe("src/app.ts");
  });

  it("does not map files outside the selected project", () => {
    expect(
      toProjectRelativePath("/Users/demo/other/app.ts", "/Users/demo/project"),
    ).toBeNull();
  });

  it("extracts the real absolute path from a preview URL", () => {
    expect(
      filePathFromPreviewUrl(
        "/api/files/preview/Users/demo/project/hello.txt?token=test",
      ),
    ).toBe("/Users/demo/project/hello.txt");
    expect(
      filePathFromPreviewUrl("/api/files/preview/C%3A/Work/Project/hello.txt"),
    ).toBe("C:/Work/Project/hello.txt");
  });

  it("parses absolute server file links, decoding percent-escapes", () => {
    expect(
      parseAbsoluteFileLink(
        "/home/ubuntu/.qwenpaw/workspaces/creator/aigc/%E5%88%86%E4%BA%AB%E7%A8%BF.md",
      ),
    ).toBe("/home/ubuntu/.qwenpaw/workspaces/creator/aigc/分享稿.md");
    expect(parseAbsoluteFileLink("/home/ubuntu/.bashrc#L3")).toBe(
      "/home/ubuntu/.bashrc",
    );
  });

  it.each([
    "/chat/abc123",
    "/settings",
    "/api/files/preview/home/ubuntu/a.png",
    "//cdn.example.com/a.js",
    "/home/ubuntu/../etc/passwd.md",
    "/bad%E0%A4%A.md",
    "relative/file.md",
    "https://example.com/a.md",
  ])("does not treat %s as an absolute file link", (href) => {
    expect(parseAbsoluteFileLink(href)).toBeNull();
  });

  it("resolves absolute references against project and workspace roots", () => {
    expect(
      rootForFileReference(
        "/Users/demo/project/src/app.ts",
        "/Users/demo/project",
        "/Users/demo/workspace",
      ),
    ).toBe("project");
    expect(
      rootForFileReference(
        "/Users/demo/workspace/PROFILE.md",
        "/Users/demo/project",
        "/Users/demo/workspace",
      ),
    ).toBe("workspace");
    expect(
      rootForFileReference(
        "C:\\Users\\demo\\workspace\\PROFILE.md",
        "C:\\Users\\demo\\project",
        "c:\\users\\demo\\workspace",
      ),
    ).toBe("workspace");
    expect(
      rootForFileReference(
        "src/app.ts",
        "/Users/demo/project",
        "/Users/demo/workspace",
      ),
    ).toBe("project");
  });
});
