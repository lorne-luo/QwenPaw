import { Bot } from "lucide-react";
import styles from "./index.module.less";
import {
  AGENT_ICON_COLORS,
  AGENT_ICONS,
  DEFAULT_ICON_COLOR,
  parseLucideIcon,
} from "./presets";

interface AgentAvatarProps {
  /**
   * `lucide:<name>:<color>` renders a filled badge; any other non-empty value
   * is shown as an emoji; empty/absent falls back to the bot icon.
   */
  icon?: string;
  size?: number;
  strokeWidth?: number;
}

export function AgentAvatar({
  icon,
  size = 16,
  strokeWidth = 2,
}: AgentAvatarProps) {
  const lucide = parseLucideIcon(icon);
  if (lucide) {
    const Icon = AGENT_ICONS[lucide.name] ?? Bot;
    const [from, to] =
      AGENT_ICON_COLORS[lucide.color] ?? AGENT_ICON_COLORS[DEFAULT_ICON_COLOR];
    return (
      <span
        className={`${styles.avatar} ${styles.badge}`}
        aria-hidden="true"
        style={{
          width: size,
          height: size,
          borderRadius: Math.round(size * 0.28),
          background: `linear-gradient(135deg, ${from}, ${to})`,
        }}
      >
        <Icon size={Math.round(size * 0.64)} strokeWidth={2.25} />
      </span>
    );
  }
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
