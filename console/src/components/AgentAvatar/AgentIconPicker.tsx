import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentAvatar } from "./index";
import {
  AGENT_ICON_COLORS,
  AGENT_ICONS,
  DEFAULT_ICON_COLOR,
  formatLucideIcon,
  parseLucideIcon,
} from "./presets";
import styles from "./AgentIconPicker.module.less";

interface AgentIconPickerProps {
  /** Injected by antd Form.Item. */
  value?: string;
  onChange?: (value: string) => void;
}

export function AgentIconPicker({ value, onChange }: AgentIconPickerProps) {
  const { t } = useTranslation();
  const selected = parseLucideIcon(value);
  // Remembered for picks made while no lucide icon is selected; otherwise the
  // selected value's own color wins, so reopening the modal on another agent
  // never shows a stale swatch.
  const [pendingColor, setPendingColor] = useState(DEFAULT_ICON_COLOR);
  const color =
    selected && AGENT_ICON_COLORS[selected.color]
      ? selected.color
      : pendingColor;

  const pickColor = (next: string) => {
    setPendingColor(next);
    if (selected) onChange?.(formatLucideIcon(selected.name, next));
  };

  return (
    <div className={styles.picker}>
      <div className={styles.colors} role="radiogroup">
        {Object.entries(AGENT_ICON_COLORS).map(([key, [from, to]]) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={key === color}
            aria-label={key}
            className={`${styles.swatch} ${key === color ? styles.active : ""}`}
            style={{ background: `linear-gradient(135deg, ${from}, ${to})` }}
            onClick={() => pickColor(key)}
          />
        ))}
      </div>
      <div className={styles.grid}>
        <button
          type="button"
          aria-pressed={!value}
          aria-label={t("agent.iconDefault")}
          title={t("agent.iconDefault")}
          className={`${styles.cell} ${!value ? styles.active : ""}`}
          onClick={() => onChange?.("")}
        >
          <AgentAvatar size={20} />
        </button>
        {Object.keys(AGENT_ICONS).map((name) => {
          const isSelected = selected?.name === name;
          return (
            <button
              key={name}
              type="button"
              aria-pressed={isSelected}
              aria-label={name}
              title={name}
              className={`${styles.cell} ${isSelected ? styles.active : ""}`}
              onClick={() => onChange?.(formatLucideIcon(name, color))}
            >
              <AgentAvatar icon={formatLucideIcon(name, color)} size={28} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
