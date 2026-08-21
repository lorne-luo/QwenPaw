/**
 * quickCreate.ts — sidebar "+" quick-create plumbing.
 *
 * Some sidebar destinations own a page-level "create" action (new cron job,
 * new skill, new MCP server…). Instead of forcing the user to navigate and
 * then hunt for the primary button, the sidebar renders a small "+" next to
 * those menu rows.
 *
 * The create dialogs live inside the (lazily loaded) pages and keep their own
 * local state, so the sidebar cannot call them directly. Two channels bridge
 * the gap — mirroring the pre-existing `qwenpaw:sidebar-new-chat` carve-out:
 *
 *   - already on the page → dispatch QUICK_CREATE_EVENT, the mounted page
 *     listens and opens its drawer/modal.
 *   - on another page      → stash the menu id in sessionStorage and navigate;
 *     the page consumes the flag when it mounts.
 *
 * Pages subscribe through the `useQuickCreate` hook.
 */

/** Window event carrying `{ menuId }` for an already-mounted page. */
export const QUICK_CREATE_EVENT = "qwenpaw:quick-create";

/** sessionStorage key holding the menu id awaiting a page mount. */
export const QUICK_CREATE_PENDING_KEY = "qwenpaw_pending_quick_create";

export interface QuickCreateSpec {
  /** i18n key for the "+" tooltip. */
  tooltipKey: string;
  /** Literal used until i18n resources resolve. */
  tooltipFallback: string;
}

/**
 * Menu ids that expose a page-level create action, keyed by menu item id.
 * Only entries listed here render a "+" in the sidebar.
 */
export const QUICK_CREATE_SPECS: Record<string, QuickCreateSpec> = {
  "core.cron-jobs": {
    tooltipKey: "sidebar.quickCreate.cronJob",
    tooltipFallback: "New cron job",
  },
  "core.skills": {
    tooltipKey: "sidebar.quickCreate.skill",
    tooltipFallback: "New skill",
  },
  "core.mcp": {
    tooltipKey: "sidebar.quickCreate.mcp",
    tooltipFallback: "Add MCP server",
  },
  "core.acp": {
    tooltipKey: "sidebar.quickCreate.acp",
    tooltipFallback: "Add ACP agent",
  },
  "core.agents": {
    tooltipKey: "sidebar.quickCreate.agent",
    tooltipFallback: "New agent",
  },
  "core.models": {
    tooltipKey: "sidebar.quickCreate.model",
    tooltipFallback: "Add provider",
  },
  "core.skill-pool": {
    tooltipKey: "sidebar.quickCreate.poolSkill",
    tooltipFallback: "New pool skill",
  },
  "core.backups": {
    tooltipKey: "sidebar.quickCreate.backup",
    tooltipFallback: "New backup",
  },
};

/**
 * Ask the page behind `path` to open its create flow.
 *
 * @param menuId      Menu item id, matched by the page's `useQuickCreate`.
 * @param path        Target route path for the menu item.
 * @param currentPath Router location currently rendered.
 * @param navigate    Router navigation callback.
 */
export function requestQuickCreate(
  menuId: string,
  path: string,
  currentPath: string,
  navigate: (to: string) => void,
): void {
  if (currentPath === path) {
    window.dispatchEvent(
      new CustomEvent(QUICK_CREATE_EVENT, { detail: { menuId } }),
    );
    return;
  }
  sessionStorage.setItem(QUICK_CREATE_PENDING_KEY, menuId);
  navigate(path);
}
