import Media from "@agentscope-ai/chat/lib/Markdown/core/components/Media";
import type { ComponentProps } from "@ant-design/x-markdown";
import { chatApi } from "../../api/modules/chat";
import { parseAbsoluteFileLink } from "../../features/files-workspace/internalFileLinks";

/**
 * Agents embed server images by path: `![](/home/me/chart.png)`. The SDK's
 * Markdown image renderer parses `src` with `new URL()` and silently renders
 * nothing for a bare path, so serve server files through the preview
 * endpoint as an absolute URL.
 */
export function MarkdownImage(props: ComponentProps) {
  const src = typeof props.src === "string" ? props.src : "";
  const filePath = parseAbsoluteFileLink(src);
  const resolvedSrc = filePath
    ? new URL(chatApi.filePreviewUrl(filePath), window.location.href).href
    : src;
  return <Media {...props} src={resolvedSrc} />;
}
