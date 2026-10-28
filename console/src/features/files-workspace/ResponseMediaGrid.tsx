import { ConfigProvider, Image, Modal } from "antd";
import type { Locale } from "antd/es/locale";
import { Music, Play } from "lucide-react";
import { useState } from "react";
import styles from "./ResponseMediaGrid.module.less";

export interface ResponseMediaItem {
  id: string;
  kind: "image" | "video" | "audio";
  name: string;
  url: string;
}

interface ResponseMediaGridProps {
  items: ResponseMediaItem[];
}

/**
 * Thumbnails for images, video and audio the agent sent with
 * ``send_file_to_user``. Images open in the antd preview overlay; video and
 * audio play in a dialog. Both close on a click outside the content.
 */
export default function ResponseMediaGrid({ items }: ResponseMediaGridProps) {
  const [playing, setPlaying] = useState<ResponseMediaItem | null>(null);

  return (
    <>
      {/* Drop the "Preview" caption antd shows on the image hover mask. */}
      <ConfigProvider locale={{ Image: { preview: "" } } as Locale}>
        <Image.PreviewGroup>
          <div className={styles.grid}>
            {items.map((item) =>
              item.kind === "image" ? (
                <Image
                  key={item.id}
                  src={item.url}
                  alt={item.name}
                  title={item.name}
                  rootClassName={styles.tile}
                  className={styles.thumb}
                />
              ) : (
                <button
                  key={item.id}
                  type="button"
                  className={styles.tile}
                  aria-label={item.name}
                  title={item.name}
                  onClick={() => setPlaying(item)}
                >
                  {item.kind === "video" ? (
                    <video
                      className={styles.thumb}
                      src={`${item.url}#t=0.1`}
                      preload="metadata"
                      muted
                      playsInline
                    />
                  ) : (
                    <span className={styles.audioThumb}>
                      <Music size={24} aria-hidden="true" />
                      <span className={styles.name}>{item.name}</span>
                    </span>
                  )}
                  <span className={styles.play} aria-hidden="true">
                    <Play size={18} fill="currentColor" />
                  </span>
                </button>
              ),
            )}
          </div>
        </Image.PreviewGroup>
      </ConfigProvider>
      <Modal
        open={playing !== null}
        title={playing?.name}
        footer={null}
        centered
        destroyOnHidden
        width={playing?.kind === "video" ? "min(960px, 92vw)" : 480}
        onCancel={() => setPlaying(null)}
      >
        {playing?.kind === "video" && (
          <video
            className={styles.videoPlayer}
            src={playing.url}
            controls
            autoPlay
            playsInline
          />
        )}
        {playing?.kind === "audio" && (
          <audio
            className={styles.audioPlayer}
            src={playing.url}
            controls
            autoPlay
          />
        )}
      </Modal>
    </>
  );
}
