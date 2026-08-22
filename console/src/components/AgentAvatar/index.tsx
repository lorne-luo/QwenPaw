import { Bot } from "lucide-react";
import styles from "./index.module.less";

interface AgentAvatarProps {
  /** Emoji configured on the agent; empty/absent falls back to the bot icon. */
  icon?: string;
  size?: number;
  strokeWidth?: number;
}

export function AgentAvatar({
  icon,
  size = 16,
  strokeWidth = 2,
}: AgentAvatarProps) {
  const emoji = icon?.trim();
  if (!emoji) return <Bot size={size} strokeWidth={strokeWidth} />;
  // Inline sizing: parent rules such as `.selectedAgentLabel span` would
  // otherwise override the font size of the emoji node.
  return (
    <span
      className={styles.avatar}
      aria-hidden="true"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.85) }}
    >
      {emoji}
    </span>
  );
}
