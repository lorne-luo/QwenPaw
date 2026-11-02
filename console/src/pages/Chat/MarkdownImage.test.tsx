// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "@ant-design/x-markdown";
import { describe, expect, it, vi } from "vitest";

vi.mock("@agentscope-ai/chat/lib/Markdown/core/components/Media", () => ({
  default: ({ src }: { src?: string }) => <img data-testid="media" src={src} />,
}));

vi.mock("../../api/modules/chat", () => ({
  chatApi: {
    filePreviewUrl: (path: string) =>
      `/api/files/preview/${path.replace(/^\/+/, "")}`,
  },
}));

import { MarkdownImage } from "./MarkdownImage";

const imageProps = (src: string) => ({ src }) as unknown as ComponentProps;
const renderedSrc = () => screen.getByTestId("media").getAttribute("src");

describe("MarkdownImage", () => {
  it("routes a server file path through the file preview endpoint", () => {
    render(<MarkdownImage {...imageProps("/home/me/chart.png")} />);

    expect(renderedSrc()).toBe(
      `${window.location.origin}/api/files/preview/home/me/chart.png`,
    );
  });

  it.each([
    "https://example.com/a.png",
    "data:image/png;base64,AAAA",
    "/api/files/preview/a.png",
    "/chat",
  ])("leaves non-server-file src untouched: %s", (src) => {
    render(<MarkdownImage {...imageProps(src)} />);

    expect(renderedSrc()).toBe(src);
  });
});
