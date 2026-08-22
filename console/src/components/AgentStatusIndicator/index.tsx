import { Tooltip } from "antd";
import { useTranslation } from "react-i18next";
import type { AgentStartupStatus } from "@/api/types/agents";
import { resolveAgentStatus } from "./status";
import type { AgentActivity } from "@/utils/agentSessionActivity";
import styles from "./index.module.less";

interface AgentStatusIndicatorProps {
  status?: AgentStartupStatus;
  enabled?: boolean;
  /** Session activity; only shown once the agent is running. */
  activity?: AgentActivity;
}

export function AgentStatusIndicator({
  status,
  enabled = true,
  activity,
}: AgentStatusIndicatorProps) {
  const { t } = useTranslation();
  const resolvedStatus = resolveAgentStatus(status, enabled);
  const shownActivity = resolvedStatus === "running" ? activity : undefined;
  const label =
    shownActivity === "generating"
      ? t("chat.statusInProgress")
      : shownActivity === "unseen"
      ? t("chat.statusUnseenResult", "New result")
      : t(`agent.status.${resolvedStatus}`);
  const variant =
    shownActivity ??
    (resolvedStatus === "running" ? "runningIdle" : resolvedStatus);

  return (
    <Tooltip title={label}>
      <span
        className={`${styles.indicator} ${styles[variant]}`}
        role="status"
        aria-label={label}
        data-status={resolvedStatus}
        data-activity={shownActivity ?? "none"}
      />
    </Tooltip>
  );
}
