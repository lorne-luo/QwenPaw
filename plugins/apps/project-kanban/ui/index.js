/**
 * Project Kanban — frontend (runtime-loaded plugin module).
 *
 * Loaded by the host via usePluginLoader (same-origin Blob URL + dynamic
 * import). Self-registers a React route at /apps/project-kanban. React and
 * antd come from window.QwenPaw.host (no bundler in this context).
 *
 * Sections:
 *   0. Bootstrap        5. Small components (avatars, counts bar)
 *   1. i18n             6. Issue card, modals, members drawer / view
 *   2. Constants        7. Screens: Projects, Workspace, root
 *   3. API              8. Route registration
 *   4. Markdown (hardened: quotes escaped, http/https links only)
 */
(function () {
  // ── 0. Bootstrap ───────────────────────────────────────────────────────
  var QwenPaw = window.QwenPaw;
  if (!QwenPaw || !QwenPaw.host || !QwenPaw.registerRoutes) {
    console.error("[project-kanban] window.QwenPaw not ready — cannot register.");
    return;
  }

  var host = QwenPaw.host;
  var React = host.React;
  var antd = host.antd;
  var h = React.createElement;
  var useHostLocale = typeof host.useLocale === "function"
    ? host.useLocale
    : function () { return "en"; };

  var Button = antd.Button;
  var Select = antd.Select;
  var Modal = antd.Modal;
  var Input = antd.Input;
  var Empty = antd.Empty;
  var Drawer = antd.Drawer;
  var Dropdown = antd.Dropdown;
  var Popconfirm = antd.Popconfirm;
  var Tooltip = antd.Tooltip;
  var Checkbox = antd.Checkbox;
  var Alert = antd.Alert;
  var message = antd.message;

  var APP_ID = "project-kanban";
  var API = "/project-kanban";
  var LAST_PROJECT_KEY = "project-kanban:lastProject";

  // ── 1. i18n ────────────────────────────────────────────────────────────
  // Both dictionaries must have the same key set (checked below at load and
  // by backend/tests/test_ui.py).
  var MESSAGES = /* i18n:begin */ {
    zh: {
      backlog: "待规划", todo: "等待调度", inProgress: "进行中", review: "审核中", done: "已完成",
      justNow: "刚刚", minutesAgo: "{count} 分钟前", hoursAgo: "{count} 小时前", daysAgo: "{count} 天前",
      appTitle: "🗂️ Project Kanban", projects: "项目", active: "进行中", archived: "已归档",
      newProject: "+ 新建项目", createFirstProject: "创建第一个项目", noProjects: "还没有项目",
      noArchivedProjects: "没有已归档的项目", noBrief: "（无简介）", runningCount: "{count} 个运行中",
      idleProject: "空闲", approvalsCount: "⚠ {count} 个待授权", refresh: "刷新", backToProjects: "← 项目",
      members: "成员", board: "🗂 看板", membersView: "🤖 成员视角", more: "更多",
      editProject: "编辑项目", archiveProject: "归档项目", unarchiveProject: "取消归档", deleteProject: "删除项目",
      deleteProjectConfirm: "删除项目「{name}」及其 {count} 个任务？此操作不可恢复。",
      deleteOnlyArchived: "只有已归档的项目才能删除", archivedBanner: "项目已归档：只读，不会调度任务。",
      projectName: "项目名称", projectKey: "项目代号", projectKeyHint: "2-6 位大写字母或数字，以字母开头；创建后不可修改",
      projectBrief: "项目简介", projectBriefHint: "作为项目背景提供给智能体", projectMembers: "成员智能体",
      nameRequired: "请填写项目名称（1-80 字）", keyInvalid: "项目代号需为 2-6 位大写字母或数字，以字母开头",
      createProject: "新建项目", save: "保存", create: "创建", cancel: "取消",
      projectCreated: "项目已创建", projectSaved: "项目已保存", projectArchived: "项目已归档",
      projectUnarchived: "项目已取消归档", projectDeleted: "项目已删除",
      addMembers: "添加成员", addMembersPlaceholder: "选择要添加的智能体", add: "添加",
      noMembers: "还没有成员", removeMember: "移除", removeMemberConfirm: "{queued} 个排队任务将退回待规划；{backlog} 个待规划任务将取消指派。确认移除？",
      memberBusyHere: "该成员正在执行本项目的任务，请先停止", memberRemoved: "已移除成员：{unassigned} 个任务取消指派，{dequeued} 个任务退回待规划",
      membersAdded: "已添加成员", openIssues: "{count} 个未完成", unknownAgent: "未知智能体（{id}）",
      leftMember: "{name}（已离开）", stateIdle: "空闲", stateRunningHere: "运行中 {key}", stateBusyIn: "忙于 {project}",
      queueLength: "队列 {count}", busy: "忙碌", idle: "空闲", tasks: "{count} 个任务", complete: "完成",
      moreItems: "+{count} 更多", noAgentsInProject: "项目还没有成员",
      unassigned: "未指派", dragHint: "拖动此处移动到其他列", awaitingApproval: "等待授权",
      runningEllipsis: "运行中…", queuedN: "排队中 #{position}", queuedBusyIn: "排队中 #{position} · {agent} 忙于 {project}",
      edit: "编辑", collapse: "收起", expandAll: "展开全部", assigneeLocked: "仅待规划/等待调度状态可切换智能体",
      updatedAt: "更新于 {time}", liveResult: "▾ 实时结果", hide: "▾ 隐藏", viewLiveResult: "▸ 查看实时结果",
      viewAgentResult: "▸ 查看 agent 结果", waitingOutput: "等待 agent 输出...", fetchResultFailed: "获取结果失败",
      executionFailed: "执行失败: {error}", toolCompleted: "调用 `{name}` 工具完成", toolStarted: "调用 `{name}` 工具",
      lastRunFailed: "上次运行失败", toolNeedsApproval: "工具需要授权", approve: "批准", deny: "拒绝",
      approved: "已批准", denied: "已拒绝", reopen: "重新打开", cancelSchedule: "取消调度", running: "运行中",
      rerun: "重新运行", markDone: "标记完成", schedule: "排队", run: "运行", stop: "停止", delete: "删除",
      deleteIssueConfirm: "删除任务 {key}？", stopped: "已停止运行", queuedAt: "智能体忙碌，已排队 #{position}",
      noIssues: "无任务", editIssue: "编辑任务 {key}", newIssue: "新建任务", title: "标题",
      descriptionOptional: "描述（可选）", assigneeOptional: "指派成员（可选）", queueNow: "立即排队",
      titleRequired: "请填写标题（1-200 字）", assignBeforeQueue: "请先指派一个成员",
      assigneeNotMemberLocal: "指派的智能体已不是项目成员", invalidMove: "不能从「{from}」移动到「{to}」",
      issueRunningLocal: "任务正在运行，请先停止", readOnly: "只读",
      "err.VALIDATION_ERROR": "输入不符合要求", "err.PROJECT_NOT_FOUND": "项目不存在",
      "err.ISSUE_NOT_FOUND": "任务不存在", "err.MEMBER_NOT_FOUND": "该智能体不是项目成员",
      "err.APPROVAL_NOT_FOUND": "授权请求不存在或已处理", "err.AGENT_NOT_FOUND": "智能体不存在",
      "err.PROJECT_KEY_TAKEN": "项目代号已被使用", "err.PROJECT_ARCHIVED": "项目已归档，无法修改",
      "err.PROJECT_NOT_ARCHIVED": "请先归档项目再删除", "err.PROJECT_HAS_RUNNING": "有任务正在运行，无法归档",
      "err.MEMBER_BUSY": "该成员正在执行本项目的任务", "err.ASSIGNEE_REQUIRED": "请先指派一个成员",
      "err.ASSIGNEE_NOT_MEMBER": "指派的智能体不是项目成员", "err.ASSIGNEE_LOCKED": "仅待规划/等待调度状态可更换指派",
      "err.INVALID_TRANSITION": "不允许的状态变更", "err.ISSUE_RUNNING": "任务正在运行，请先停止",
      "err.APPROVAL_UNAVAILABLE": "授权服务不可用", "err.BOARD_UNAVAILABLE": "看板数据不可用（board.json 无法读取）",
      "err.generic": "请求失败（HTTP {status}）", "err.network": "网络错误，请重试"
    },
    en: {
      backlog: "Backlog", todo: "Queued", inProgress: "In Progress", review: "In Review", done: "Done",
      justNow: "just now", minutesAgo: "{count} min ago", hoursAgo: "{count} hr ago", daysAgo: "{count} day(s) ago",
      appTitle: "🗂️ Project Kanban", projects: "Projects", active: "Active", archived: "Archived",
      newProject: "+ New project", createFirstProject: "Create your first project", noProjects: "No projects yet",
      noArchivedProjects: "No archived projects", noBrief: "(No brief)", runningCount: "{count} running",
      idleProject: "Idle", approvalsCount: "⚠ {count} approval(s)", refresh: "Refresh", backToProjects: "← Projects",
      members: "Members", board: "🗂 Board", membersView: "🤖 Members", more: "More",
      editProject: "Edit project", archiveProject: "Archive project", unarchiveProject: "Unarchive", deleteProject: "Delete project",
      deleteProjectConfirm: "Delete project \"{name}\" and its {count} issue(s)? This cannot be undone.",
      deleteOnlyArchived: "Only archived projects can be deleted", archivedBanner: "This project is archived: read-only, nothing is dispatched.",
      projectName: "Project name", projectKey: "Project key", projectKeyHint: "2-6 uppercase letters or digits, starting with a letter; cannot change later",
      projectBrief: "Project brief", projectBriefHint: "Shared with agents as project context", projectMembers: "Member agents",
      nameRequired: "Enter a project name (1-80 characters)", keyInvalid: "The key must be 2-6 uppercase letters or digits, starting with a letter",
      createProject: "New project", save: "Save", create: "Create", cancel: "Cancel",
      projectCreated: "Project created", projectSaved: "Project saved", projectArchived: "Project archived",
      projectUnarchived: "Project unarchived", projectDeleted: "Project deleted",
      addMembers: "Add members", addMembersPlaceholder: "Select agents to add", add: "Add",
      noMembers: "No members yet", removeMember: "Remove", removeMemberConfirm: "{queued} queued issue(s) will move back to Backlog; {backlog} backlog issue(s) will be unassigned. Remove?",
      memberBusyHere: "This member is running an issue in this project; stop it first", memberRemoved: "Member removed: {unassigned} issue(s) unassigned, {dequeued} moved back to Backlog",
      membersAdded: "Members added", openIssues: "{count} open", unknownAgent: "Unknown agent ({id})",
      leftMember: "{name} (left)", stateIdle: "Idle", stateRunningHere: "Running here ({key})", stateBusyIn: "Busy in {project}",
      queueLength: "Queue {count}", busy: "Busy", idle: "Idle", tasks: "{count} issue(s)", complete: "Complete",
      moreItems: "+{count} more", noAgentsInProject: "This project has no members",
      unassigned: "Unassigned", dragHint: "Drag here to move to another column", awaitingApproval: "Awaiting approval",
      runningEllipsis: "Running…", queuedN: "Queued #{position}", queuedBusyIn: "Queued #{position} · {agent} busy in {project}",
      edit: "Edit", collapse: "Collapse", expandAll: "Show all", assigneeLocked: "The assignee can only change in Backlog or Queued",
      updatedAt: "Updated {time}", liveResult: "▾ Live result", hide: "▾ Hide", viewLiveResult: "▸ View live result",
      viewAgentResult: "▸ View agent result", waitingOutput: "Waiting for agent output...", fetchResultFailed: "Failed to fetch result",
      executionFailed: "Execution failed: {error}", toolCompleted: "Completed tool `{name}`", toolStarted: "Called tool `{name}`",
      lastRunFailed: "Last run failed", toolNeedsApproval: "Tool approval required", approve: "Approve", deny: "Deny",
      approved: "Approved", denied: "Denied", reopen: "Reopen", cancelSchedule: "Cancel", running: "Running",
      rerun: "Run again", markDone: "Mark done", schedule: "Queue", run: "Run", stop: "Stop", delete: "Delete",
      deleteIssueConfirm: "Delete issue {key}?", stopped: "Run stopped", queuedAt: "Agent busy — queued #{position}",
      noIssues: "No issues", editIssue: "Edit issue {key}", newIssue: "New issue", title: "Title",
      descriptionOptional: "Description (optional)", assigneeOptional: "Assign a member (optional)", queueNow: "Queue now",
      titleRequired: "Enter a title (1-200 characters)", assignBeforeQueue: "Assign a member first",
      assigneeNotMemberLocal: "The assignee is no longer a project member", invalidMove: "Cannot move from \"{from}\" to \"{to}\"",
      issueRunningLocal: "The issue is running; stop it first", readOnly: "Read-only",
      "err.VALIDATION_ERROR": "The input is not valid", "err.PROJECT_NOT_FOUND": "Project not found",
      "err.ISSUE_NOT_FOUND": "Issue not found", "err.MEMBER_NOT_FOUND": "That agent is not a project member",
      "err.APPROVAL_NOT_FOUND": "Approval request not found or already handled", "err.AGENT_NOT_FOUND": "Agent not found",
      "err.PROJECT_KEY_TAKEN": "That project key is already used", "err.PROJECT_ARCHIVED": "The project is archived and cannot be changed",
      "err.PROJECT_NOT_ARCHIVED": "Archive the project before deleting it", "err.PROJECT_HAS_RUNNING": "An issue is running; the project cannot be archived",
      "err.MEMBER_BUSY": "This member is running an issue in this project", "err.ASSIGNEE_REQUIRED": "Assign a member first",
      "err.ASSIGNEE_NOT_MEMBER": "The assignee is not a project member", "err.ASSIGNEE_LOCKED": "The assignee can only change in Backlog or Queued",
      "err.INVALID_TRANSITION": "That status change is not allowed", "err.ISSUE_RUNNING": "The issue is running; stop it first",
      "err.APPROVAL_UNAVAILABLE": "The approval service is unavailable", "err.BOARD_UNAVAILABLE": "Board data unavailable (board.json cannot be read)",
      "err.generic": "Request failed (HTTP {status})", "err.network": "Network error, please retry"
    }
  } /* i18n:end */;

  function normalizeLocale(value) {
    return String(value || "").toLowerCase().split("-")[0] === "zh" ? "zh" : "en";
  }

  function tr(locale, key, values) {
    var template = (MESSAGES[locale] && MESSAGES[locale][key]) || MESSAGES.en[key] || key;
    return template.replace(/\{(\w+)\}/g, function (_, name) {
      return values && values[name] !== undefined ? String(values[name]) : "{" + name + "}";
    });
  }

  function missingKeys() {
    var zh = MESSAGES.zh;
    var en = MESSAGES.en;
    return Object.keys(en).filter(function (k) { return !(k in zh); })
      .concat(Object.keys(zh).filter(function (k) { return !(k in en); }));
  }
  if (missingKeys().length) {
    console.error("[project-kanban] i18n key sets differ:", missingKeys());
  }

  // ── 2. Constants ───────────────────────────────────────────────────────
  var C = {
    text: "#1f2937",
    sub: "#6b7280",
    muted: "#9ca3af",
    cardBg: "#ffffff",
    cardBorder: "rgba(15,23,42,0.08)",
    boardBg: "#fafafb",
    accent: "#4f46e5",
  };

  var COLUMNS = [
    { key: "backlog", labelKey: "backlog", dot: "#9ca3af", tint: "rgba(148,163,184,0.20)", hover: "rgba(148,163,184,0.35)" },
    { key: "todo", labelKey: "todo", dot: "#94a3b8", tint: "rgba(148,163,184,0.12)", hover: "rgba(148,163,184,0.25)" },
    { key: "in_progress", labelKey: "inProgress", dot: "#f59e0b", tint: "rgba(245,158,11,0.18)", hover: "rgba(245,158,11,0.30)" },
    { key: "review", labelKey: "review", dot: "#22c55e", tint: "rgba(34,197,94,0.16)", hover: "rgba(34,197,94,0.28)" },
    { key: "done", labelKey: "done", dot: "#3b82f6", tint: "rgba(59,130,246,0.16)", hover: "rgba(59,130,246,0.28)" },
  ];
  var COLUMN_DOT = {};
  var COLUMN_LABEL = {};
  COLUMNS.forEach(function (c) {
    COLUMN_DOT[c.key] = c.dot;
    COLUMN_LABEL[c.key] = c.labelKey;
  });

  // Manual moves accepted by PATCH (requirements §8.2); in_progress is
  // entered through /run only.
  var PATCH_MOVES = {
    "backlog>todo": true, "review>todo": true, "done>todo": true,
    "todo>backlog": true, "review>done": true, "backlog>done": true,
    "todo>done": true, "done>review": true, "review>backlog": true,
    "done>backlog": true,
  };
  var RUN_FROM = { backlog: true, todo: true, review: true };
  var KEY_RE = /^[A-Z][A-Z0-9]{1,5}$/;

  /**
   * Check a board move against §8.2 before the optimistic update.
   * Returns {action: "patch"|"run"|"none"} or {warning: key, values}.
   */
  function checkMove(issue, target, memberIds) {
    var from = issue.status;
    if (from === target) return { action: "none" };
    if (from === "in_progress") return { warning: "issueRunningLocal" };
    var isRun = target === "in_progress";
    if (!isRun && !PATCH_MOVES[from + ">" + target]) {
      return { warning: "invalidMove", from: from, to: target };
    }
    if (isRun && !RUN_FROM[from]) {
      return { warning: "invalidMove", from: from, to: target };
    }
    if (isRun || target === "todo") {
      if (!issue.assignee) return { warning: "assignBeforeQueue" };
      if (memberIds.indexOf(issue.assignee) < 0) return { warning: "assigneeNotMemberLocal" };
    }
    return { action: isRun ? "run" : "patch" };
  }

  /** Same derivation as the backend (rules.derive_key). */
  function deriveKey(name, taken) {
    var used = {};
    (taken || []).forEach(function (k) { used[k] = true; });
    var words = String(name || "").match(/[A-Za-z][A-Za-z0-9]*/g) || [];
    var base = "";
    if (words.length >= 2) {
      base = words.slice(0, 4).map(function (w) { return w[0]; }).join("").toUpperCase();
    } else if (words.length === 1) {
      base = words[0].slice(0, 3).toUpperCase();
    }
    if (!KEY_RE.test(base)) {
      var n = 1;
      while (used["P" + n]) n++;
      return "P" + n;
    }
    if (!used[base]) return base;
    for (var s = 2; s < 100; s++) {
      if (!used[base + s]) return base + s;
    }
    return base;
  }

  // ── 3. API ─────────────────────────────────────────────────────────────
  function ApiError(status, code, detail) {
    this.name = "ApiError";
    this.status = status;
    this.code = code || "";
    this.message = detail || ("HTTP " + status);
  }
  ApiError.prototype = Object.create(Error.prototype);

  function authHeaders(json) {
    var token = host.getApiToken ? host.getApiToken() : "";
    var headers = {};
    if (json) headers["Content-Type"] = "application/json";
    if (token) headers["Authorization"] = "Bearer " + token;
    return headers;
  }

  function apiFetch(path, opts) {
    opts = opts || {};
    return fetch(host.getApiUrl(path), {
      method: opts.method || "GET",
      headers: authHeaders(true),
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return null; }).then(function (body) {
          var detail = body && body.detail;
          var code = detail && typeof detail === "object" ? detail.code : "";
          var text = detail && typeof detail === "object" ? detail.message : detail;
          throw new ApiError(res.status, code, text);
        });
      }
      return res.status === 204 ? null : res.json();
    });
  }

  // EventSource cannot set headers; the backend accepts ?token= (see README).
  function apiStreamUrl(path) {
    var url = host.getApiUrl(path);
    var token = host.getApiToken ? host.getApiToken() : "";
    if (token) url += (url.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(token);
    return url;
  }

  /** Localized text for an API error: err.<CODE>, else generic + status. */
  function errorText(t, err) {
    if (err && err.code && MESSAGES.en["err." + err.code]) return t("err." + err.code);
    if (err && err.status) return t("err.generic", { status: err.status });
    return t("err.network");
  }

  function enc(value) {
    return encodeURIComponent(value);
  }

  var api = {
    listProjects: function () { return apiFetch(API + "/projects?archived=all"); },
    getProject: function (pid) { return apiFetch(API + "/projects/" + enc(pid)); },
    createProject: function (body) { return apiFetch(API + "/projects", { method: "POST", body: body }); },
    patchProject: function (pid, body) { return apiFetch(API + "/projects/" + enc(pid), { method: "PATCH", body: body }); },
    archiveProject: function (pid) { return apiFetch(API + "/projects/" + enc(pid) + "/archive", { method: "POST" }); },
    unarchiveProject: function (pid) { return apiFetch(API + "/projects/" + enc(pid) + "/unarchive", { method: "POST" }); },
    deleteProject: function (pid) { return apiFetch(API + "/projects/" + enc(pid), { method: "DELETE" }); },
    addMembers: function (pid, ids) {
      return apiFetch(API + "/projects/" + enc(pid) + "/members", { method: "POST", body: { agent_ids: ids } });
    },
    removeMember: function (pid, agentId) {
      return apiFetch(API + "/projects/" + enc(pid) + "/members/" + enc(agentId), { method: "DELETE" });
    },
    listIssues: function (pid) { return apiFetch(API + "/projects/" + enc(pid) + "/issues"); },
    createIssue: function (pid, body) {
      return apiFetch(API + "/projects/" + enc(pid) + "/issues", { method: "POST", body: body });
    },
    patchIssue: function (pid, iid, body) {
      return apiFetch(API + "/projects/" + enc(pid) + "/issues/" + enc(iid), { method: "PATCH", body: body });
    },
    deleteIssue: function (pid, iid) {
      return apiFetch(API + "/projects/" + enc(pid) + "/issues/" + enc(iid), { method: "DELETE" });
    },
    runIssue: function (pid, iid, language) {
      return apiFetch(API + "/projects/" + enc(pid) + "/issues/" + enc(iid) + "/run?language=" + enc(language), { method: "POST" });
    },
    stopIssue: function (pid, iid) {
      return apiFetch(API + "/projects/" + enc(pid) + "/issues/" + enc(iid) + "/stop", { method: "POST" });
    },
    result: function (pid, iid) {
      return apiFetch(API + "/projects/" + enc(pid) + "/issues/" + enc(iid) + "/result");
    },
    streamUrl: function (pid, iid) {
      return apiStreamUrl(API + "/projects/" + enc(pid) + "/issues/" + enc(iid) + "/stream");
    },
    listApprovals: function (pid) { return apiFetch(API + "/projects/" + enc(pid) + "/approvals"); },
    approve: function (pid, rid) {
      return apiFetch(API + "/projects/" + enc(pid) + "/approvals/" + enc(rid) + "/approve", { method: "POST" });
    },
    deny: function (pid, rid) {
      return apiFetch(API + "/projects/" + enc(pid) + "/approvals/" + enc(rid) + "/deny", { method: "POST" });
    },
    approvalSummary: function () { return apiFetch(API + "/approvals/summary"); },
    dispatch: function () { return apiFetch(API + "/dispatch"); },
    listAgents: function () { return apiFetch("/agents"); },
  };

  /**
   * Live-output connections (R-UI-11), kept outside React so the reconnect
   * logic is testable. A dropped connection does not end a run: if the issue
   * is still in_progress the stream is reopened with backoff, and the text is
   * rebuilt from the server's replay of the run (it replaces the old text on
   * the first event, so nothing is appended twice). A run that has ended
   * (done/error event, or a closed stream for an issue no longer running)
   * finishes the stream and reloads the board.
   *
   * opts: open(id) -> EventSource-like; isRunning(id) -> Promise<boolean>;
   * onText(id, text | null); onFinished(id); label(event) -> string;
   * schedule(fn, ms) -> handle; cancel(handle).
   */
  var STREAM_RETRY_MS = 1000;
  var STREAM_RETRY_MAX_MS = 15000;

  function createStreamManager(opts) {
    var conns = {};

    function stop(id) {
      var c = conns[id];
      if (!c) return;
      delete conns[id];
      if (c.timer !== null) opts.cancel(c.timer);
      if (c.source) {
        try { c.source.close(); } catch (e) {}
      }
      opts.onText(id, null);
    }

    function finish(id) {
      stop(id);
      opts.onFinished(id);
    }

    function retry(c) {
      var delay = Math.min(STREAM_RETRY_MS * Math.pow(2, c.attempt), STREAM_RETRY_MAX_MS);
      c.attempt += 1;
      c.timer = opts.schedule(function () {
        c.timer = null;
        if (conns[c.id] === c) connect(c);
      }, delay);
    }

    function connect(c) {
      var source = opts.open(c.id);
      c.source = source;
      c.replaying = true;
      source.onmessage = function (e) {
        if (c.source !== source) return;
        var msg;
        try { msg = JSON.parse(e.data); } catch (err) { return; }
        c.attempt = 0;
        if (msg.type === "done" || msg.type === "error") {
          finish(c.id);
          return;
        }
        if (c.replaying) {
          c.replaying = false;
          c.text = ""; // the replay restarts the run's output from the top
        }
        var line = msg.type === "message" ? msg.text : opts.label(msg);
        if (line) {
          c.text += line + "\n";
          opts.onText(c.id, c.text);
        }
      };
      source.onerror = function () {
        if (c.source !== source) return;
        try { source.close(); } catch (e) {}
        c.source = null;
        opts.isRunning(c.id).then(
          function (running) {
            if (conns[c.id] !== c) return;
            if (running) retry(c);
            else finish(c.id);
          },
          function () {
            if (conns[c.id] === c) retry(c); // offline: try again later
          },
        );
      };
    }

    return {
      subscribe: function (id) {
        stop(id);
        var c = { id: id, text: "", attempt: 0, timer: null, source: null, replaying: true };
        conns[id] = c;
        opts.onText(id, "");
        connect(c);
      },
      unsubscribe: stop,
      closeAll: function () { Object.keys(conns).forEach(stop); },
    };
  }

  // ── 4. Markdown (hardened, R-SEC-2) ────────────────────────────────────
  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Render a small Markdown subset to HTML. Everything is escaped
   * (including quotes) before any markup is added; links are emitted only
   * for http:/https: URLs, anything else stays plain text.
   */
  function renderMarkdown(text) {
    if (!text) return "";
    var slots = [];
    function slot(html) {
      slots.push(html);
      return "\u0000" + (slots.length - 1) + "\u0000";
    }
    var src = String(text).replace(/\u0000/g, "");
    src = src.replace(/```([\s\S]*?)```/g, function (_, code) {
      return slot('<pre style="background:#f1f5f9;padding:8px;border-radius:4px;margin:4px 0;overflow-x:auto"><code>' +
        escapeHtml(code.replace(/^\w*\n/, "").trim()) + "</code></pre>");
    });
    src = src.replace(/`([^`\n]+)`/g, function (_, code) {
      return slot('<code style="background:#f1f5f9;padding:2px 4px;border-radius:3px;font-family:monospace;font-size:11px">' +
        escapeHtml(code) + "</code>");
    });
    var html = escapeHtml(src);
    html = html.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, function (match, label, url) {
      if (!/^https?:\/\//i.test(url) || url.indexOf("\u0000") >= 0) return match;
      return slot('<a href="' + url + '" target="_blank" rel="noopener noreferrer" style="color:#4f46e5;text-decoration:underline">' +
        label + "</a>");
    });
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/^### (.+)$/gm, '<div style="font-size:13px;font-weight:600;margin:8px 0 4px 0">$1</div>');
    html = html.replace(/^## (.+)$/gm, '<div style="font-size:14px;font-weight:600;margin:8px 0 4px 0">$1</div>');
    html = html.replace(/^# (.+)$/gm, '<div style="font-size:15px;font-weight:600;margin:8px 0 4px 0">$1</div>');
    html = html.replace(/^[*-] (.+)$/gm, '<div style="margin-left:16px">• $1</div>');
    html = html.replace(/\n/g, "<br>");
    return html.replace(/\u0000(\d+)\u0000/g, function (_, i) { return slots[Number(i)]; });
  }

  // ── 5. Small helpers and components ────────────────────────────────────
  function relTime(ts, locale) {
    if (!ts) return "";
    var diff = Date.now() / 1000 - ts;
    if (diff < 60) return tr(locale, "justNow");
    if (diff < 3600) return tr(locale, "minutesAgo", { count: Math.floor(diff / 60) });
    if (diff < 86400) return tr(locale, "hoursAgo", { count: Math.floor(diff / 3600) });
    return tr(locale, "daysAgo", { count: Math.floor(diff / 86400) });
  }

  function findAgent(agents, id) {
    for (var i = 0; i < agents.length; i++) {
      if (agents[i].id === id) return agents[i];
    }
    return null;
  }

  function agentName(agents, id) {
    var agent = findAgent(agents, id);
    return agent ? agent.name || id : id;
  }

  function agentColor(id) {
    var palette = ["#6366f1", "#22c55e", "#f59e0b", "#3b82f6", "#ec4899", "#14b8a6", "#8b5cf6", "#ef4444"];
    var s = 0;
    var key = id || "x";
    for (var i = 0; i < key.length; i++) s = (s * 31 + key.charCodeAt(i)) >>> 0;
    return palette[s % palette.length];
  }

  function memberIdsOf(project) {
    return ((project && project.members) || []).map(function (m) { return m.agent_id; });
  }

  function Avatar(props) {
    var size = props.size || 24;
    var name = props.name || props.id || "?";
    var color = agentColor(props.id);
    return h(
      "span",
      {
        title: name,
        style: {
          width: size, height: size, borderRadius: "50%", flexShrink: 0,
          background: "linear-gradient(135deg, " + color + ", " + color + "cc)",
          color: "#fff", fontWeight: 700, fontSize: Math.round(size * 0.42),
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          border: "2px solid #fff", marginLeft: props.overlap ? -6 : 0,
        },
      },
      name.slice(0, 1).toUpperCase(),
    );
  }

  function AvatarStack(props) {
    var ids = props.ids || [];
    var max = props.max || 5;
    var shown = ids.slice(0, max);
    return h(
      "span",
      { style: { display: "inline-flex", alignItems: "center" } },
      shown.map(function (id, idx) {
        return h(Avatar, { key: id, id: id, name: agentName(props.agents, id), size: props.size || 22, overlap: idx > 0 });
      }),
      ids.length > max
        ? h("span", { style: { fontSize: 11, color: C.sub, marginLeft: 4 } }, "+" + (ids.length - max))
        : null,
    );
  }

  function Dot(props) {
    return h("span", {
      style: {
        width: props.size || 8, height: props.size || 8, borderRadius: "50%",
        background: props.color, display: "inline-block", flexShrink: 0,
      },
    });
  }

  function KeyBadge(props) {
    return h(
      "span",
      {
        style: {
          fontFamily: "monospace", fontSize: 12, fontWeight: 700, color: C.accent,
          background: "#eef2ff", borderRadius: 6, padding: "2px 7px",
        },
      },
      props.text,
    );
  }

  function CountsBar(props) {
    var counts = props.counts || {};
    var total = 0;
    COLUMNS.forEach(function (c) { total += counts[c.key] || 0; });
    return h(
      "div",
      { style: { display: "flex", height: 6, borderRadius: 999, overflow: "hidden", background: "#eef2f7" } },
      total
        ? COLUMNS.map(function (c) {
            var n = counts[c.key] || 0;
            if (!n) return null;
            return h("div", {
              key: c.key,
              title: props.t(c.labelKey) + ": " + n,
              style: { width: (n / total) * 100 + "%", background: c.dot },
            });
          })
        : null,
    );
  }

  var CSS =
    ".pk-card{transition:transform .15s ease,box-shadow .15s ease}" +
    ".pk-card:hover{transform:translateY(-2px);box-shadow:0 10px 26px rgba(15,23,42,0.10)}" +
    "@keyframes pkPulse{0%,100%{box-shadow:0 0 0 0 rgba(34,197,94,0.45)}50%{box-shadow:0 0 0 6px rgba(34,197,94,0)}}" +
    ".pk-pulse{animation:pkPulse 1.5s ease-in-out infinite}" +
    "@keyframes pkSpin{to{transform:rotate(360deg)}}" +
    ".pk-spin{animation:pkSpin .8s linear infinite}" +
    "@keyframes pkBlink{50%{opacity:0}}" +
    ".pk-caret{animation:pkBlink 1s step-end infinite}";

  /** Text summary of /result messages (tool calls + assistant text). */
  function summarizeMessages(messages, t) {
    var summary = [];
    (messages || []).forEach(function (msg) {
      var type = msg.type || "";
      if (typeof type === "object" && type.value) type = type.value;
      type = String(type);
      if (type === "reasoning") return;
      if (type.indexOf("plugin_call") >= 0 || type.indexOf("function_call") >= 0 || type.indexOf("mcp_tool_call") >= 0) {
        var name = "";
        (msg.content || []).forEach(function (item) {
          if (!name && item && item.data && item.data.name) name = item.data.name;
        });
        if (name && name !== "assistant") {
          summary.push(t(type.indexOf("_output") >= 0 ? "toolCompleted" : "toolStarted", { name: name }));
        }
      } else if (type === "message" && msg.role === "assistant") {
        (msg.content || []).forEach(function (item) {
          if (item && item.type === "text" && item.text) summary.push(item.text);
        });
      }
    });
    return summary.join("\n");
  }

  // ── 6. Issue card ──────────────────────────────────────────────────────
  function IssueCard(props) {
    var issue = props.issue;
    var t = props.t;
    var readOnly = props.readOnly;
    var running = issue.status === "in_progress";
    var pending = props.pendingApprovals || [];
    var waitingApproval = running && pending.length > 0;
    var lastRun = issue.last_run || null;
    var failed = lastRun && lastRun.outcome === "failed" && issue.status === "backlog";

    var expandedState = React.useState(false);
    var expanded = expandedState[0];
    var setExpanded = expandedState[1];
    var resultState = React.useState(null);
    var fetched = resultState[0];
    var setFetched = resultState[1];
    var descState = React.useState(false);
    var descExpanded = descState[0];
    var setDescExpanded = descState[1];
    var errorState = React.useState(false);
    var errorExpanded = errorState[0];
    var setErrorExpanded = errorState[1];
    var dragState = React.useState(false);
    var dragging = dragState[0];
    var setDragging = dragState[1];
    var cardRef = React.useRef(null);

    var liveText = props.streamText;
    var hasLive = running && typeof liveText === "string" && liveText.length > 0;
    var resultRef = React.useRef(null);
    var stickRef = React.useRef(true);

    React.useEffect(function () {
      var el = resultRef.current;
      if (el && hasLive && stickRef.current) el.scrollTop = el.scrollHeight;
    }, [liveText, expanded]);

    React.useEffect(function () {
      if (expanded && !running && !fetched) {
        api.result(props.projectId, issue.id)
          .then(function (data) { setFetched(data || {}); })
          .catch(function () { setFetched({ error: t("fetchResultFailed") }); });
      }
    }, [expanded, running, issue.id, fetched]);

    React.useEffect(function () { setFetched(null); }, [issue.status, issue.updated_at]);

    var displayResult = "";
    if (hasLive) {
      displayResult = liveText;
    } else if (fetched) {
      displayResult = fetched.error
        ? t("executionFailed", { error: fetched.error })
        : summarizeMessages(fetched.messages, t);
    }

    var memberIds = props.memberIds;
    var assignee = issue.assignee || "";
    var isFormer = assignee && memberIds.indexOf(assignee) < 0;
    var assigneeName = assignee ? agentName(props.agents, assignee) : "";
    var options = [{ label: t("unassigned"), value: "" }].concat(
      memberIds.map(function (id) { return { label: agentName(props.agents, id), value: id }; }),
    );
    if (isFormer) options.push({ label: t("leftMember", { name: assigneeName }), value: assignee });
    var canAssign = !readOnly && (issue.status === "backlog" || issue.status === "todo");

    var info = props.queueInfo || {};
    var hint = null;
    if (waitingApproval) {
      hint = h("span", { style: { fontSize: 12, color: "#dc2626", fontWeight: 600 } }, t("awaitingApproval"));
    } else if (running) {
      hint = h("span", { style: { fontSize: 12, color: "#d97706" } }, t("runningEllipsis"));
    } else if (issue.status === "todo" && info.position) {
      var busyElsewhere = info.running && info.running.project_id !== props.projectId ? info.running : null;
      hint = h(
        "span",
        { style: { fontSize: 12, color: "#6366f1", textAlign: "right" } },
        busyElsewhere
          ? t("queuedBusyIn", { position: info.position, agent: assigneeName, project: busyElsewhere.project_name })
          : t("queuedN", { position: info.position }),
      );
    }

    function button(label, onClick, extra) {
      return h(Button, Object.assign({ size: "small", onClick: onClick, disabled: readOnly }, extra || {}), label);
    }

    var actions = [];
    if (issue.status === "backlog") {
      actions.push(button(info.running ? t("schedule") : t("run"), function () { props.onRun(issue); },
        { key: "run", type: "primary", disabled: readOnly || !assignee || isFormer }));
    } else if (issue.status === "todo") {
      actions.push(button(t("cancelSchedule"), function () { props.onMove(issue, "backlog"); }, { key: "cancel", danger: true }));
    } else if (running) {
      actions.push(h(Button, { key: "running", size: "small", type: "primary", loading: true, disabled: true }, t("running")));
      actions.push(button(t("stop"), function () { props.onStop(issue); }, { key: "stop", danger: true }));
    } else if (issue.status === "review") {
      actions.push(button(t("rerun"), function () { props.onRun(issue); }, { key: "rerun", disabled: readOnly || isFormer || !assignee }));
      actions.push(button(t("markDone"), function () { props.onMove(issue, "done"); }, { key: "done", type: "primary" }));
    } else if (issue.status === "done") {
      actions.push(button(t("reopen"), function () { props.onMove(issue, "backlog"); }, { key: "reopen" }));
    }
    if (!running) {
      actions.push(h(
        Popconfirm,
        {
          key: "delete",
          title: t("deleteIssueConfirm", { key: issue.key }),
          okText: t("delete"),
          cancelText: t("cancel"),
          okButtonProps: { danger: true },
          disabled: readOnly,
          onConfirm: function () { props.onDelete(issue); },
        },
        h(Button, { size: "small", type: "text", danger: true, disabled: readOnly, style: { marginLeft: "auto" } }, t("delete")),
      ));
    }

    return h(
      "div",
      {
        ref: cardRef,
        style: {
          background: C.cardBg, border: "1px solid " + C.cardBorder, borderRadius: 10,
          padding: "10px 12px", marginBottom: 10,
          boxShadow: dragging ? "0 4px 6px rgba(15,23,42,0.15)" : "0 1px 2px rgba(15,23,42,0.05)",
          opacity: dragging ? 0.4 : 1, transition: "opacity 0.2s ease, box-shadow 0.2s ease",
        },
      },
      // Header: drag handle (status dot + key) + one state hint.
      h(
        "div",
        {
          draggable: !readOnly,
          onDragStart: function (e) {
            e.dataTransfer.setData("text/plain", issue.id);
            e.dataTransfer.effectAllowed = "move";
            if (cardRef.current) e.dataTransfer.setDragImage(cardRef.current, 0, 0);
            setDragging(true);
          },
          onDragEnd: function () { setDragging(false); },
          title: readOnly ? t("readOnly") : t("dragHint"),
          style: {
            display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8,
            marginBottom: 6, cursor: readOnly ? "default" : dragging ? "grabbing" : "grab",
          },
        },
        h(
          "span",
          { style: { display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.muted, fontFamily: "monospace" } },
          h(Dot, { color: COLUMN_DOT[issue.status] || C.muted }),
          issue.key,
        ),
        hint,
      ),
      // Title + edit
      h(
        "div",
        { style: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 2 } },
        h("div", { style: { fontWeight: 600, color: C.text, fontSize: 14, flex: 1, wordBreak: "break-word" } }, issue.title),
        issue.status === "backlog" && !readOnly
          ? h("a", {
              style: { fontSize: 12, color: C.accent, cursor: "pointer", marginLeft: 8, flexShrink: 0 },
              title: t("edit"),
              onClick: function (e) { e.stopPropagation(); props.onEdit(issue); },
            }, "✏️")
          : null,
      ),
      issue.description
        ? h(
            "div",
            { style: { marginBottom: 8 } },
            h("div", {
              style: Object.assign(
                { fontSize: 12, color: C.sub, lineHeight: 1.5, whiteSpace: "pre-wrap", overflow: "hidden" },
                descExpanded
                  ? { maxHeight: 220, overflowY: "auto" }
                  : { display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical" },
              ),
            }, issue.description),
            issue.description.length > 100 || issue.description.split("\n").length > 3
              ? h("a", {
                  style: { fontSize: 12, color: C.accent, cursor: "pointer" },
                  onClick: function () { setDescExpanded(!descExpanded); },
                }, descExpanded ? t("collapse") : t("expandAll"))
              : null,
          )
        : h("div", { style: { height: 6 } }),
      // Failed last run badge (expands to the error text).
      failed
        ? h(
            "div",
            { style: { marginBottom: 8 } },
            h("a", {
              style: { fontSize: 12, color: "#b91c1c", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 6, padding: "1px 8px", cursor: "pointer" },
              onClick: function () { setErrorExpanded(!errorExpanded); },
            }, "⚠ " + t("lastRunFailed")),
            errorExpanded
              ? h("div", {
                  style: { fontSize: 12, color: "#991b1b", marginTop: 4, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 160, overflowY: "auto" },
                }, lastRun.error || "")
              : null,
          )
        : null,
      // Footer: assignee select + updated time.
      h(
        "div",
        { style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 } },
        h(
          "div",
          { style: { display: "flex", alignItems: "center", gap: 6, minWidth: 0, flex: 1 } },
          assignee ? h(Avatar, { id: assignee, name: assigneeName, size: 20 }) : h("span", {
            style: { width: 20, height: 20, borderRadius: "50%", background: "#f1f5f9", display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 11, flexShrink: 0 },
          }, "○"),
          h(Select, {
            size: "small",
            variant: "borderless",
            disabled: !canAssign,
            title: canAssign ? undefined : t("assigneeLocked"),
            value: assignee,
            style: { flex: 1, minWidth: 0, marginLeft: -4 },
            options: options,
            onChange: function (val) { props.onAssign(issue, val); },
          }),
        ),
        h("span", { style: { fontSize: 11, color: C.muted, whiteSpace: "nowrap" } }, t("updatedAt", { time: relTime(issue.updated_at, props.locale) })),
      ),
      // Result (live while running; from /result otherwise).
      running || issue.status === "review" || issue.status === "done" || (lastRun && lastRun.outcome === "stopped")
        ? h(
            "div",
            { style: { marginTop: 6 } },
            h("a", {
              style: { fontSize: 12, color: C.accent },
              onClick: function () {
                var next = !expanded;
                setExpanded(next);
                if (next && running) props.onSubscribe(issue.id);
                if (!next && running) props.onUnsubscribe(issue.id);
              },
            }, expanded ? (hasLive ? t("liveResult") : t("hide")) : running ? t("viewLiveResult") : t("viewAgentResult")),
            expanded
              ? h("div", {
                  ref: resultRef,
                  onScroll: function (e) {
                    var el = e.currentTarget;
                    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
                  },
                  style: {
                    fontSize: 12, color: C.sub, background: "#f8fafc", border: "1px solid " + C.cardBorder,
                    borderRadius: 6, padding: 8, marginTop: 4, maxHeight: 220, overflowY: "auto", lineHeight: "1.6",
                    wordBreak: "break-word",
                  },
                  dangerouslySetInnerHTML: {
                    __html: renderMarkdown(displayResult || (running ? t("waitingOutput") : "")) +
                      (hasLive ? '<span class="pk-caret" style="margin-left:2px;color:#f59e0b;font-weight:700">▋</span>' : ""),
                  },
                })
              : null,
          )
        : null,
      // Approval panel.
      waitingApproval
        ? h(
            "div",
            { style: { marginTop: 8, padding: "8px 10px", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8 } },
            pending.map(function (ap) {
              return h(
                "div",
                {
                  key: ap.request_id,
                  style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: pending.length > 1 ? 6 : 0 },
                },
                h(
                  "div",
                  { style: { fontSize: 12, color: "#991b1b", flex: 1, minWidth: 0 } },
                  h("div", { style: { fontWeight: 600 } }, t("toolNeedsApproval")),
                  h("div", { style: { color: "#b91c1c", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, ap.display_name || ap.tool_name),
                ),
                h(
                  "div",
                  { style: { display: "flex", gap: 4, flexShrink: 0 } },
                  h(Button, { size: "small", type: "primary", onClick: function () { props.onApprove(ap.request_id); } }, t("approve")),
                  h(Button, { size: "small", danger: true, onClick: function () { props.onDeny(ap.request_id); } }, t("deny")),
                ),
              );
            }),
          )
        : null,
      // Actions.
      h(
        "div",
        { style: { display: "flex", alignItems: "center", gap: 4, marginTop: 8, paddingTop: 8, borderTop: "1px solid #f1f5f9" } },
        actions,
      ),
    );
  }

  // ── 6b. Modals ─────────────────────────────────────────────────────────
  function IssueModal(props) {
    var t = props.t;
    var editing = props.issue || null;
    var formState = React.useState({
      title: editing ? editing.title : "",
      description: editing ? editing.description || "" : "",
      assignee: "",
      queueNow: false,
    });
    var form = formState[0];
    var setForm = formState[1];
    var busyState = React.useState(false);
    var submitting = busyState[0];
    var setSubmitting = busyState[1];
    function set(patch) { setForm(Object.assign({}, form, patch)); }
    function send(body) {
      setSubmitting(true);
      Promise.resolve(props.onSubmit(body)).then(function () { setSubmitting(false); }, function () { setSubmitting(false); });
    }
    function submit() {
      if (submitting) return;
      var title = form.title.trim();
      if (!title || title.length > 200) {
        message.warning(t("titleRequired"));
        return;
      }
      send({
        title: title,
        description: form.description,
        assignee: form.assignee,
        queueNow: form.queueNow && !!form.assignee,
      });
    }
    return h(
      Modal,
      {
        open: true,
        title: editing ? t("editIssue", { key: editing.key }) : t("newIssue"),
        okText: editing ? t("save") : t("create"),
        cancelText: t("cancel"),
        okButtonProps: { loading: submitting },
        onOk: submit,
        onCancel: props.onCancel,
      },
      h(
        "div",
        { style: { display: "flex", flexDirection: "column", gap: 12, paddingTop: 8 } },
        h(Input, { placeholder: t("title"), value: form.title, maxLength: 200, onChange: function (e) { set({ title: e.target.value }); } }),
        h(Input.TextArea, { placeholder: t("descriptionOptional"), rows: 4, maxLength: 8000, value: form.description, onChange: function (e) { set({ description: e.target.value }); } }),
        editing
          ? null
          : h(Select, {
              placeholder: t("assigneeOptional"),
              style: { width: "100%" },
              value: form.assignee,
              options: [{ label: t("unassigned"), value: "" }].concat(
                props.memberIds.map(function (id) { return { label: agentName(props.agents, id), value: id }; }),
              ),
              onChange: function (val) { set({ assignee: val, queueNow: val ? form.queueNow : false }); },
            }),
        editing
          ? null
          : h(Checkbox, {
              checked: form.queueNow,
              disabled: !form.assignee,
              onChange: function (e) { set({ queueNow: e.target.checked }); },
            }, t("queueNow")),
      ),
    );
  }

  function ProjectModal(props) {
    var t = props.t;
    var editing = props.project || null;
    var formState = React.useState({
      name: editing ? editing.name : "",
      key: editing ? editing.key : "",
      keyTouched: !!editing,
      description: editing ? editing.description || "" : "",
      members: [],
    });
    var form = formState[0];
    var setForm = formState[1];
    var busyState = React.useState(false);
    var submitting = busyState[0];
    var setSubmitting = busyState[1];
    function set(patch) { setForm(Object.assign({}, form, patch)); }
    function send(body) {
      setSubmitting(true);
      Promise.resolve(props.onSubmit(body)).then(function () { setSubmitting(false); }, function () { setSubmitting(false); });
    }
    function onName(value) {
      var patch = { name: value };
      if (!form.keyTouched) patch.key = value.trim() ? deriveKey(value, props.takenKeys) : "";
      set(patch);
    }
    function submit() {
      if (submitting) return;
      var name = form.name.trim();
      if (!name || name.length > 80) {
        message.warning(t("nameRequired"));
        return;
      }
      if (editing) {
        send({ name: name, description: form.description });
        return;
      }
      var key = form.key.trim().toUpperCase();
      if (key && !KEY_RE.test(key)) {
        message.warning(t("keyInvalid"));
        return;
      }
      send({ name: name, key: key || undefined, description: form.description, members: form.members });
    }
    function label(text, hint) {
      return h(
        "div",
        { style: { fontSize: 12, color: C.sub, marginBottom: 4 } },
        text,
        hint ? h("span", { style: { color: C.muted, marginLeft: 6 } }, hint) : null,
      );
    }
    return h(
      Modal,
      {
        open: true,
        title: editing ? t("editProject") : t("createProject"),
        okText: editing ? t("save") : t("create"),
        cancelText: t("cancel"),
        okButtonProps: { loading: submitting },
        onOk: submit,
        onCancel: props.onCancel,
      },
      h(
        "div",
        { style: { display: "flex", flexDirection: "column", gap: 12, paddingTop: 8 } },
        h("div", null, label(t("projectName")), h(Input, { value: form.name, maxLength: 80, onChange: function (e) { onName(e.target.value); } })),
        h(
          "div",
          null,
          label(t("projectKey"), t("projectKeyHint")),
          h(Input, {
            value: form.key,
            disabled: !!editing,
            maxLength: 6,
            style: { fontFamily: "monospace", width: 160 },
            status: form.key && !KEY_RE.test(form.key.toUpperCase()) ? "error" : undefined,
            onChange: function (e) { set({ key: e.target.value.toUpperCase(), keyTouched: true }); },
          }),
        ),
        h(
          "div",
          null,
          label(t("projectBrief"), t("projectBriefHint")),
          h(Input.TextArea, { rows: 5, maxLength: 4000, value: form.description, onChange: function (e) { set({ description: e.target.value }); } }),
        ),
        editing
          ? null
          : h(
              "div",
              null,
              label(t("projectMembers")),
              h(Select, {
                mode: "multiple",
                style: { width: "100%" },
                value: form.members,
                placeholder: t("addMembersPlaceholder"),
                options: props.agents.map(function (a) { return { label: a.name || a.id, value: a.id }; }),
                onChange: function (val) { set({ members: val }); },
              }),
            ),
      ),
    );
  }

  // ── 6c. Members: state helpers, drawer, members view ───────────────────
  function memberState(dispatch, agentId, projectId) {
    var entry = (dispatch.agents || {})[agentId] || { running: null, queue: [] };
    var running = entry.running;
    var state = "idle";
    if (running) state = running.project_id === projectId ? "here" : "elsewhere";
    return { state: state, running: running, queue: entry.queue || [] };
  }

  function stateLabel(t, ms) {
    if (ms.state === "here") return t("stateRunningHere", { key: ms.running.key });
    if (ms.state === "elsewhere") return t("stateBusyIn", { project: ms.running.project_name });
    return t("stateIdle");
  }

  function StateTag(props) {
    var ms = props.memberState;
    var busy = ms.state !== "idle";
    return h(
      "span",
      {
        className: ms.state === "here" ? "pk-pulse" : null,
        style: {
          display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, fontWeight: 500,
          color: ms.state === "here" ? "#15803d" : busy ? "#b45309" : C.sub,
          background: ms.state === "here" ? "rgba(34,197,94,0.12)" : busy ? "rgba(245,158,11,0.12)" : "#f1f5f9",
          borderRadius: 999, padding: "3px 9px", whiteSpace: "nowrap", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis",
        },
      },
      h(Dot, { size: 7, color: ms.state === "here" ? "#22c55e" : busy ? "#f59e0b" : "#cbd5e1" }),
      stateLabel(props.t, ms),
    );
  }

  function MembersDrawer(props) {
    var t = props.t;
    var project = props.project;
    var readOnly = props.readOnly;
    var memberIds = memberIdsOf(project);
    var pickState = React.useState([]);
    var picked = pickState[0];
    var setPicked = pickState[1];
    var candidates = props.agents.filter(function (a) { return memberIds.indexOf(a.id) < 0; });
    return h(
      Drawer,
      { open: props.open, onClose: props.onClose, title: t("members"), width: 440 },
      h(
        "div",
        { style: { display: "flex", gap: 8, marginBottom: 16 } },
        h(Select, {
          mode: "multiple",
          style: { flex: 1 },
          value: picked,
          disabled: readOnly,
          placeholder: t("addMembersPlaceholder"),
          options: candidates.map(function (a) { return { label: a.name || a.id, value: a.id }; }),
          onChange: setPicked,
        }),
        h(Button, {
          type: "primary",
          disabled: readOnly || !picked.length,
          onClick: function () { props.onAdd(picked).then(function (ok) { if (ok) setPicked([]); }); },
        }, t("add")),
      ),
      memberIds.length === 0
        ? h(Empty, { image: Empty.PRESENTED_IMAGE_SIMPLE, description: t("noMembers") })
        : memberIds.map(function (id) {
            var agent = findAgent(props.agents, id);
            var ms = memberState(props.dispatch, id, project.id);
            var mine = props.issues.filter(function (i) { return i.assignee === id; });
            var open = mine.filter(function (i) { return i.status !== "done"; }).length;
            var queued = mine.filter(function (i) { return i.status === "todo"; }).length;
            var backlog = mine.filter(function (i) { return i.status === "backlog"; }).length;
            var busyHere = ms.state === "here";
            var removeButton = h(Button, { size: "small", danger: true, disabled: readOnly || busyHere }, t("removeMember"));
            return h(
              "div",
              { key: id, style: { display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderBottom: "1px solid #f1f5f9" } },
              h(Avatar, { id: id, name: agent ? agent.name || id : id, size: 32 }),
              h(
                "div",
                { style: { flex: 1, minWidth: 0 } },
                h("div", { style: { fontWeight: 600, color: C.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } },
                  agent ? agent.name || id : t("unknownAgent", { id: id })),
                h("div", { style: { fontSize: 11, color: C.muted, fontFamily: "monospace" } }, id),
                h("div", { style: { display: "flex", gap: 6, alignItems: "center", marginTop: 4, flexWrap: "wrap" } },
                  h(StateTag, { t: t, memberState: ms }),
                  h("span", { style: { fontSize: 11, color: C.sub } }, t("openIssues", { count: open }))),
              ),
              busyHere
                ? h(Tooltip, { title: t("memberBusyHere") }, h("span", { style: { display: "inline-block" } }, removeButton))
                : h(Popconfirm, {
                    title: t("removeMemberConfirm", { queued: queued, backlog: backlog }),
                    okText: t("removeMember"),
                    cancelText: t("cancel"),
                    okButtonProps: { danger: true },
                    disabled: readOnly,
                    onConfirm: function () { return props.onRemove(id); },
                  }, removeButton),
            );
          }),
    );
  }

  var LANE_BUCKETS = [
    { key: "backlog", labelKey: "backlog", statuses: ["backlog"], dot: "#9ca3af" },
    { key: "todo", labelKey: "todo", statuses: ["todo"], dot: "#94a3b8" },
    { key: "running", labelKey: "running", statuses: ["in_progress"], dot: "#f59e0b" },
    { key: "review", labelKey: "review", statuses: ["review"], dot: "#22c55e" },
    { key: "done", labelKey: "complete", statuses: ["done"], dot: "#3b82f6" },
  ];

  function MemberLane(props) {
    var t = props.t;
    var items = props.items || [];
    var id = props.agentId;
    return h(
      "div",
      {
        className: "pk-card",
        style: { background: "#ffffff", border: "1px solid " + C.cardBorder, borderRadius: 16, padding: 14, display: "flex", flexDirection: "column", gap: 12 },
      },
      h(
        "div",
        { style: { display: "flex", alignItems: "center", gap: 10 } },
        id ? h(Avatar, { id: id, name: props.name, size: 38 }) : h("span", { style: { width: 38, height: 38, borderRadius: "50%", background: "#f1f5f9", display: "inline-flex", alignItems: "center", justifyContent: "center", color: C.muted } }, "○"),
        h(
          "div",
          { style: { minWidth: 0, flex: 1 } },
          h("div", { style: { fontWeight: 600, color: C.text, fontSize: 14, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, props.name),
          h("div", { style: { fontSize: 11, color: C.muted } },
            t("tasks", { count: items.length }) + (props.memberState ? " · " + t("queueLength", { count: props.memberState.queue.length }) : "")),
        ),
        props.memberState ? h(StateTag, { t: t, memberState: props.memberState }) : null,
      ),
      h(
        "div",
        { style: { display: "flex", flexDirection: "column", gap: 8 } },
        LANE_BUCKETS.map(function (b) {
          var list = items.filter(function (i) { return b.statuses.indexOf(i.status) >= 0; });
          return h(
            "div",
            { key: b.key, style: { background: "#f8fafc", border: "1px solid #eef2f7", borderRadius: 10, padding: "8px 10px" } },
            h(
              "div",
              { style: { display: "flex", alignItems: "center", gap: 7, marginBottom: list.length ? 6 : 0 } },
              h(Dot, { color: b.dot }),
              h("span", { style: { fontSize: 12, fontWeight: 600, color: C.text } }, t(b.labelKey)),
              h("span", { style: { marginLeft: "auto", fontSize: 11, color: C.sub, background: "#eef2f7", borderRadius: 999, padding: "1px 8px", fontWeight: 600 } }, list.length),
            ),
            list.length
              ? h(
                  "div",
                  { style: { display: "flex", flexDirection: "column", gap: 4 } },
                  list.slice(0, 5).map(function (it) {
                    return h(
                      "div",
                      { key: it.id, style: { fontSize: 12, color: C.sub, display: "flex", alignItems: "center", gap: 6, minWidth: 0 } },
                      it.status === "in_progress"
                        ? h("span", { className: "pk-spin", style: { width: 9, height: 9, borderRadius: "50%", border: "2px solid #f59e0b", borderTopColor: "transparent", display: "inline-block", flexShrink: 0 } })
                        : h(Dot, { size: 5, color: b.dot }),
                      h("span", { style: { fontFamily: "monospace", color: C.muted, flexShrink: 0 } }, it.key),
                      h("span", { style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, it.title),
                    );
                  }),
                  list.length > 5 ? h("div", { style: { fontSize: 11, color: C.muted } }, t("moreItems", { count: list.length - 5 })) : null,
                )
              : null,
          );
        }),
      ),
    );
  }

  function MembersView(props) {
    var t = props.t;
    var memberIds = memberIdsOf(props.project);
    var byAgent = {};
    var unassigned = [];
    var former = {};
    memberIds.forEach(function (id) { byAgent[id] = []; });
    props.issues.forEach(function (i) {
      if (i.assignee && byAgent[i.assignee]) byAgent[i.assignee].push(i);
      else if (i.assignee) (former[i.assignee] = former[i.assignee] || []).push(i);
      else unassigned.push(i);
    });
    var busy = 0;
    var lanes = memberIds.map(function (id) {
      var ms = memberState(props.dispatch, id, props.project.id);
      if (ms.state !== "idle") busy++;
      return h(MemberLane, { key: id, t: t, agentId: id, name: agentName(props.agents, id), items: byAgent[id], memberState: ms });
    });
    Object.keys(former).forEach(function (id) {
      lanes.push(h(MemberLane, {
        key: "__left__" + id, t: t, agentId: id,
        name: t("leftMember", { name: agentName(props.agents, id) }), items: former[id],
      }));
    });
    if (unassigned.length) {
      lanes.push(h(MemberLane, { key: "__unassigned__", t: t, agentId: "", name: t("unassigned"), items: unassigned }));
    }
    function chip(color, bg, textColor, text, pulse) {
      return h(
        "div",
        { style: { display: "inline-flex", alignItems: "center", gap: 6, background: bg, borderRadius: 999, padding: "6px 12px" } },
        h("span", { className: pulse ? "pk-pulse" : null, style: { width: 8, height: 8, borderRadius: "50%", background: color, display: "inline-block" } }),
        h("span", { style: { fontSize: 13, fontWeight: 600, color: textColor } }, text),
      );
    }
    return h(
      "div",
      { style: { flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" } },
      h(
        "div",
        {
          style: {
            background: "linear-gradient(120deg,#eef2ff 0%,#ecfdf5 100%)", border: "1px solid rgba(99,102,241,0.15)", borderRadius: 16,
            padding: "14px 18px", marginBottom: 14, display: "flex", gap: 10, flexWrap: "wrap",
          },
        },
        chip("#22c55e", "rgba(34,197,94,0.12)", "#15803d", t("busy") + " " + busy, busy > 0),
        chip("#cbd5e1", "#f1f5f9", C.sub, t("idle") + " " + (memberIds.length - busy), false),
      ),
      lanes.length === 0
        ? h(Empty, { description: h("span", { style: { color: C.muted } }, t("noAgentsInProject")) })
        : h(
            "div",
            { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 14, overflowY: "auto", alignContent: "flex-start", alignItems: "start", flex: 1, paddingBottom: 4 } },
            lanes,
          ),
    );
  }

  function ViewToggle(props) {
    return h(
      "div",
      { style: { display: "inline-flex", background: "#eef2f7", borderRadius: 10, padding: 3, gap: 2 } },
      props.options.map(function (o) {
        var active = props.value === o.key;
        return h(
          "div",
          {
            key: o.key,
            onClick: function () { props.onChange(o.key); },
            style: {
              cursor: "pointer", fontSize: 13, fontWeight: active ? 600 : 500, padding: "4px 12px", borderRadius: 8,
              background: active ? "#ffffff" : "transparent", color: active ? C.text : C.sub,
              boxShadow: active ? "0 1px 2px rgba(15,23,42,0.12)" : "none", transition: "all .15s ease", whiteSpace: "nowrap",
            },
          },
          o.label,
        );
      }),
    );
  }

  // ── 7. Screens ─────────────────────────────────────────────────────────
  function ProjectsScreen(props) {
    var t = props.t;
    var tabState = React.useState("active");
    var tab = tabState[0];
    var setTab = tabState[1];
    var projects = (props.projects || []).filter(function (p) { return tab === "archived" ? p.archived : !p.archived; });
    var header = h(
      "div",
      { style: { display: "flex", alignItems: "center", gap: 16, marginBottom: 16, marginRight: 100, flexWrap: "wrap" } },
      h("div", { style: { fontSize: 18, fontWeight: 700, color: C.text } }, t("appTitle")),
      h(ViewToggle, {
        value: tab,
        onChange: setTab,
        options: [{ key: "active", label: t("active") }, { key: "archived", label: t("archived") }],
      }),
      h("span", { style: { flex: 1 } }),
      h(Button, { size: "small", onClick: props.onRefresh }, t("refresh")),
      h(Button, { size: "small", type: "primary", onClick: props.onCreate }, t("newProject")),
    );
    var body;
    if (props.error) {
      body = h(Alert, { type: "error", showIcon: true, message: props.error });
    } else if (props.projects === null) {
      body = h("div", { style: { padding: 40, color: C.muted } }, "…");
    } else if (!projects.length) {
      body = h(Empty, {
        style: { marginTop: 80 },
        description: h("span", { style: { color: C.muted } }, tab === "archived" ? t("noArchivedProjects") : t("noProjects")),
      }, tab === "archived" ? null : h(Button, { type: "primary", onClick: props.onCreate }, t("createFirstProject")));
    } else {
      body = h(
        "div",
        { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 14, overflowY: "auto", alignContent: "flex-start", paddingBottom: 8 } },
        projects.map(function (p) {
          var runningCount = (p.counts && p.counts.in_progress) || 0;
          var pending = (props.approvals || {})[p.id] || 0;
          return h(
            "div",
            {
              key: p.id,
              className: "pk-card",
              onClick: function () { props.onOpen(p.id); },
              style: {
                cursor: "pointer", background: C.cardBg, border: "1px solid " + C.cardBorder, borderRadius: 14,
                padding: 14, display: "flex", flexDirection: "column", gap: 10, opacity: p.archived ? 0.8 : 1,
              },
            },
            h(
              "div",
              { style: { display: "flex", alignItems: "center", gap: 8, minWidth: 0 } },
              h(KeyBadge, { text: p.key }),
              h("span", { style: { fontWeight: 600, color: C.text, fontSize: 15, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, p.name),
            ),
            h("div", {
              style: { fontSize: 12, color: p.description ? C.sub : C.muted, lineHeight: 1.5, minHeight: 36, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", whiteSpace: "pre-wrap" },
            }, p.description || t("noBrief")),
            h(AvatarStack, { ids: memberIdsOf(p), agents: props.agents, max: 5 }),
            h(CountsBar, { counts: p.counts, t: t }),
            h(
              "div",
              { style: { display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 12 } },
              h(
                "span",
                { style: { display: "inline-flex", alignItems: "center", gap: 6, color: runningCount ? "#d97706" : C.muted } },
                h(Dot, { color: runningCount ? "#f59e0b" : "#d1d5db" }),
                runningCount ? t("runningCount", { count: runningCount }) : t("idleProject"),
              ),
              pending ? h("span", { style: { color: "#dc2626", fontWeight: 600 } }, t("approvalsCount", { count: pending })) : null,
            ),
          );
        }),
      );
    }
    return h(React.Fragment, null, header, body);
  }

  function Workspace(props) {
    var t = props.t;
    var locale = props.locale;
    var pid = props.projectId;
    var agents = props.agents;

    var projectState = React.useState(null);
    var project = projectState[0];
    var setProject = projectState[1];
    var issuesState = React.useState([]);
    var issues = issuesState[0];
    var setIssues = issuesState[1];
    var dispatchState = React.useState({ agents: {} });
    var dispatch = dispatchState[0];
    var setDispatch = dispatchState[1];
    var approvalsState = React.useState({});
    var approvals = approvalsState[0];
    var setApprovals = approvalsState[1];
    var viewState = React.useState("board");
    var view = viewState[0];
    var setView = viewState[1];
    var drawerState = React.useState(false);
    var drawerOpen = drawerState[0];
    var setDrawerOpen = drawerState[1];
    var issueModalState = React.useState(null);
    var issueModal = issueModalState[0];
    var setIssueModal = issueModalState[1];
    var projectModalState = React.useState(false);
    var projectModal = projectModalState[0];
    var setProjectModal = projectModalState[1];
    var dragOverState = React.useState(null);
    var dragOver = dragOverState[0];
    var setDragOver = dragOverState[1];
    var streamState = React.useState({});
    var streams = streamState[0];
    var setStreams = streamState[1];
    var loadErrorState = React.useState(null);
    var loadError = loadErrorState[0];
    var setLoadError = loadErrorState[1];
    var pidRef = React.useRef(pid);
    pidRef.current = pid;
    var seqRef = React.useRef(0);
    // Timers and EventSource handlers outlive a render; read t through a ref.
    var tRef = React.useRef(t);
    tRef.current = t;
    var loadRef = React.useRef(load);
    loadRef.current = load;
    var streamsRef = React.useRef(null);
    if (streamsRef.current === null) {
      streamsRef.current = createStreamManager({
        open: function (id) { return new window.EventSource(api.streamUrl(pidRef.current, id)); },
        isRunning: function (id) {
          return api.listIssues(pidRef.current).then(function (data) {
            return ((data && data.issues) || []).some(function (i) {
              return i.id === id && i.status === "in_progress";
            });
          });
        },
        onText: function (id, text) {
          setStreams(function (prev) {
            if (text === null && !(id in prev)) return prev;
            var next = Object.assign({}, prev);
            if (text === null) delete next[id];
            else next[id] = text;
            return next;
          });
        },
        onFinished: function () { loadRef.current(); },
        label: function (msg) {
          if (msg.type === "tool_start") return tRef.current("toolStarted", { name: msg.name });
          if (msg.type === "tool_done") return tRef.current("toolCompleted", { name: msg.name });
          return "";
        },
        schedule: function (fn, ms) { return window.setTimeout(fn, ms); },
        cancel: function (handle) { window.clearTimeout(handle); },
      });
    }

    function fail(err) {
      message.error(errorText(tRef.current, err));
    }

    function load() {
      var forPid = pid;
      var seq = ++seqRef.current;
      function current() { return pidRef.current === forPid && seqRef.current === seq; }
      return Promise.all([api.getProject(forPid), api.listIssues(forPid), api.dispatch()])
        .then(function (res) {
          if (!current()) return;
          setProject(res[0]);
          setIssues((res[1] && res[1].issues) || []);
          setDispatch(res[2] || { agents: {} });
          setLoadError(null);
        })
        .catch(function (err) {
          if (!current()) return;
          if (err && err.status === 404) props.onMissing();
          else setLoadError(errorText(tRef.current, err));
        });
    }

    function loadApprovals() {
      var forPid = pid;
      api.listApprovals(forPid)
        .then(function (data) { if (pidRef.current === forPid) setApprovals((data && data.approvals) || {}); })
        .catch(function () {});
    }

    function closeStream(id) {
      streamsRef.current.unsubscribe(id);
    }

    function closeAllStreams() {
      streamsRef.current.closeAll();
    }

    function subscribe(id) {
      if (typeof window.EventSource === "undefined") return;
      streamsRef.current.subscribe(id);
    }

    React.useEffect(function () {
      setProject(null);
      setIssues([]);
      setApprovals({});
      setLoadError(null);
      setView("board");
      load();
      loadApprovals();
      var boardTimer = setInterval(load, 4000);
      var approvalTimer = setInterval(loadApprovals, 3000);
      return function () {
        clearInterval(boardTimer);
        clearInterval(approvalTimer);
        closeAllStreams();
      };
    }, [pid]);

    if (!project) {
      return loadError
        ? h("div", null,
            h(Button, { size: "small", type: "text", onClick: props.onBack, style: { marginBottom: 12 } }, t("backToProjects")),
            h(Alert, { type: "error", showIcon: true, message: loadError }))
        : h("div", { style: { padding: 40, color: C.muted } }, "…");
    }

    var readOnly = !!project.archived;
    var memberIds = memberIdsOf(project);
    var working = issues.filter(function (i) { return i.status === "in_progress"; }).length;

    function queueInfo(issue) {
      if (!issue.assignee) return {};
      var entry = (dispatch.agents || {})[issue.assignee];
      if (!entry) return {};
      var position = 0;
      (entry.queue || []).forEach(function (ref, idx) { if (ref.issue_id === issue.id) position = idx + 1; });
      return { position: position, running: entry.running };
    }

    function patch(issue, body, optimistic) {
      if (optimistic) {
        setIssues(function (prev) {
          return prev.map(function (i) { return i.id === issue.id ? Object.assign({}, i, optimistic) : i; });
        });
      }
      return api.patchIssue(pid, issue.id, body).then(load).catch(function (err) { fail(err); load(); });
    }

    function runIssue(issue) {
      api.runIssue(pid, issue.id, locale)
        .then(function (res) {
          if (res && !res.started && res.queue_position) message.info(t("queuedAt", { position: res.queue_position }));
          load();
        })
        .catch(function (err) { fail(err); load(); });
    }

    function moveIssue(id, target) {
      var issue = issues.find(function (i) { return i.id === id; });
      if (!issue || readOnly) return;
      var check = checkMove(issue, target, memberIds);
      if (check.warning) {
        message.warning(t(check.warning, { from: t(COLUMN_LABEL[check.from] || ""), to: t(COLUMN_LABEL[check.to] || "") }));
        return;
      }
      if (check.action === "run") runIssue(issue);
      else if (check.action === "patch") patch(issue, { status: target }, { status: target });
    }

    function onAssign(issue, assignee) {
      patch(issue, { assignee: assignee }, { assignee: assignee });
    }

    function onStop(issue) {
      closeStream(issue.id);
      api.stopIssue(pid, issue.id)
        .then(function () { message.info(t("stopped")); load(); })
        .catch(function (err) { fail(err); load(); });
    }

    function onDelete(issue) {
      setIssues(function (prev) { return prev.filter(function (i) { return i.id !== issue.id; }); });
      api.deleteIssue(pid, issue.id).then(load).catch(function (err) { fail(err); load(); });
    }

    function submitIssue(form) {
      var request = issueModal && issueModal.issue
        ? api.patchIssue(pid, issueModal.issue.id, { title: form.title, description: form.description })
        : api.createIssue(pid, {
            title: form.title,
            description: form.description,
            assignee: form.assignee,
            status: form.queueNow ? "todo" : "backlog",
            language: locale,
          });
      return request.then(function () { setIssueModal(null); load(); }).catch(fail);
    }

    function resolveApproval(rid, approve) {
      (approve ? api.approve(pid, rid) : api.deny(pid, rid))
        .then(function () { message.success(t(approve ? "approved" : "denied")); loadApprovals(); })
        .catch(function (err) { fail(err); loadApprovals(); });
    }

    function projectAction(key) {
      if (key === "edit") setProjectModal(true);
      else if (key === "archive") {
        api.archiveProject(pid).then(function () { message.success(t("projectArchived")); load(); props.onChanged(); }).catch(fail);
      } else if (key === "unarchive") {
        api.unarchiveProject(pid).then(function () { message.success(t("projectUnarchived")); load(); props.onChanged(); }).catch(fail);
      } else if (key === "delete") {
        Modal.confirm({
          title: t("deleteProject"),
          content: t("deleteProjectConfirm", { name: project.name, count: issues.length }),
          okText: t("delete"),
          cancelText: t("cancel"),
          okButtonProps: { danger: true },
          onOk: function () {
            return api.deleteProject(pid).then(function () {
              message.success(t("projectDeleted"));
              props.onChanged();
              props.onBack();
            }).catch(fail);
          },
        });
      }
    }

    function addMembers(ids) {
      return api.addMembers(pid, ids)
        .then(function () { message.success(t("membersAdded")); load(); return true; })
        .catch(function (err) { fail(err); return false; });
    }

    function removeMember(agentId) {
      return api.removeMember(pid, agentId)
        .then(function (res) {
          message.success(t("memberRemoved", { unassigned: res.unassigned, dequeued: res.dequeued }));
          load();
        })
        .catch(fail);
    }

    var switcherOptions = (props.projects || [])
      .filter(function (p) { return !p.archived || p.id === pid; })
      .map(function (p) { return { label: p.key + " · " + p.name, value: p.id }; });

    var menuItems = [
      { key: "edit", label: t("editProject"), disabled: readOnly },
      readOnly ? { key: "unarchive", label: t("unarchiveProject") } : { key: "archive", label: t("archiveProject") },
      { type: "divider" },
      { key: "delete", label: readOnly ? t("deleteProject") : h(Tooltip, { title: t("deleteOnlyArchived") }, t("deleteProject")), danger: true, disabled: !readOnly },
    ];

    var header = h(
      "div",
      { style: { display: "flex", alignItems: "center", gap: 12, marginBottom: 14, marginRight: 100, flexWrap: "wrap" } },
      h(Button, { size: "small", type: "text", onClick: props.onBack }, t("backToProjects")),
      h(Select, {
        size: "small",
        value: pid,
        style: { minWidth: 220, maxWidth: 320 },
        options: switcherOptions,
        onChange: props.onSwitch,
      }),
      h(AvatarStack, { ids: memberIds, agents: agents, max: 5 }),
      h(Button, { size: "small", onClick: function () { setDrawerOpen(true); } }, t("members")),
      h(ViewToggle, {
        value: view,
        onChange: setView,
        options: [{ key: "board", label: t("board") }, { key: "members", label: t("membersView") }],
      }),
      h(
        "span",
        { style: { fontSize: 13, color: working ? "#d97706" : C.muted, display: "inline-flex", alignItems: "center", gap: 6 } },
        h(Dot, { color: working ? "#f59e0b" : "#d1d5db" }),
        t("runningCount", { count: working }),
      ),
      h(Button, { size: "small", onClick: load }, t("refresh")),
      h(Dropdown, { trigger: ["click"], menu: { items: menuItems, onClick: function (e) { projectAction(e.key); } } },
        h(Button, { size: "small", title: t("more") }, "⋯")),
    );

    var banner = readOnly
      ? h(Alert, {
          type: "warning",
          showIcon: true,
          style: { marginBottom: 12 },
          message: t("archivedBanner"),
          action: h(Button, { size: "small", onClick: function () { projectAction("unarchive"); } }, t("unarchiveProject")),
        })
      : null;

    var board = h(
      "div",
      { style: { display: "flex", gap: 14, flex: 1, overflowX: "auto", alignItems: "stretch", paddingBottom: 4 } },
      COLUMNS.map(function (col) {
        var colIssues = issues.filter(function (i) { return i.status === col.key; });
        var hovered = dragOver === col.key;
        return h(
          "div",
          {
            key: col.key,
            onDragOver: function (e) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; },
            onDragEnter: function (e) { e.preventDefault(); if (!readOnly) setDragOver(col.key); },
            onDragLeave: function (e) { if (e.currentTarget === e.target) setDragOver(null); },
            onDrop: function (e) {
              e.preventDefault();
              var id = e.dataTransfer.getData("text/plain");
              setDragOver(null);
              if (id) moveIssue(id, col.key);
            },
            style: {
              flex: "1 1 260px", minWidth: 240, maxWidth: 420, borderRadius: 14, padding: "12px 10px",
              maxHeight: "100%", display: "flex", flexDirection: "column", transition: "all 0.2s ease",
              background: hovered
                ? "linear-gradient(180deg, " + col.tint + " 0%, " + col.hover + " 20%, rgba(255,255,255,0) 60%), #ffffff"
                : "linear-gradient(180deg, " + col.tint + " 0%, rgba(255,255,255,0) 42%), #ffffff",
              border: hovered ? "2px dashed " + col.dot : "1px solid rgba(15,23,42,0.05)",
            },
          },
          h(
            "div",
            { style: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12, padding: "0 4px" } },
            h(
              "span",
              { style: { fontWeight: 600, color: C.text, display: "inline-flex", alignItems: "center", gap: 7 } },
              h(Dot, { size: 9, color: col.dot }),
              t(col.labelKey),
              h("span", { style: { color: C.muted, marginLeft: 4, fontWeight: 400 } }, colIssues.length),
            ),
            col.key === "backlog" && !readOnly
              ? h(Button, { type: "text", size: "small", style: { color: C.muted }, onClick: function () { setIssueModal({}); } }, "+")
              : null,
          ),
          h(
            "div",
            { style: { overflowY: "auto", flex: 1, minHeight: 80 } },
            colIssues.length === 0
              ? h(Empty, {
                  image: Empty.PRESENTED_IMAGE_SIMPLE,
                  description: h("span", { style: { color: C.muted } }, t("noIssues")),
                  style: { margin: "40px 0", opacity: 0.6 },
                })
              : colIssues.map(function (issue) {
                  return h(IssueCard, {
                    key: issue.id,
                    issue: issue,
                    projectId: pid,
                    agents: agents,
                    memberIds: memberIds,
                    locale: locale,
                    t: t,
                    readOnly: readOnly,
                    queueInfo: queueInfo(issue),
                    pendingApprovals: approvals[issue.id] || [],
                    streamText: streams[issue.id],
                    onAssign: onAssign,
                    onMove: function (iss, status) { moveIssue(iss.id, status); },
                    onRun: runIssue,
                    onStop: onStop,
                    onDelete: onDelete,
                    onEdit: function (iss) { setIssueModal({ issue: iss }); },
                    onSubscribe: subscribe,
                    onUnsubscribe: closeStream,
                    onApprove: function (rid) { resolveApproval(rid, true); },
                    onDeny: function (rid) { resolveApproval(rid, false); },
                  });
                }),
          ),
        );
      }),
    );

    return h(
      React.Fragment,
      null,
      header,
      banner,
      view === "members"
        ? h(MembersView, { t: t, project: project, issues: issues, agents: agents, dispatch: dispatch })
        : board,
      h(MembersDrawer, {
        t: t,
        open: drawerOpen,
        onClose: function () { setDrawerOpen(false); },
        project: project,
        issues: issues,
        agents: agents,
        dispatch: dispatch,
        readOnly: readOnly,
        onAdd: addMembers,
        onRemove: removeMember,
      }),
      issueModal
        ? h(IssueModal, {
            t: t,
            issue: issueModal.issue,
            agents: agents,
            memberIds: memberIds,
            onSubmit: submitIssue,
            onCancel: function () { setIssueModal(null); },
          })
        : null,
      projectModal
        ? h(ProjectModal, {
            t: t,
            project: project,
            agents: agents,
            takenKeys: [],
            onCancel: function () { setProjectModal(false); },
            onSubmit: function (body) {
              return api.patchProject(pid, body)
                .then(function () { message.success(t("projectSaved")); setProjectModal(false); load(); props.onChanged(); })
                .catch(fail);
            },
          })
        : null,
    );
  }

  function readLastProject() {
    try { return window.localStorage.getItem(LAST_PROJECT_KEY) || null; } catch (e) { return null; }
  }

  function writeLastProject(id) {
    try {
      if (id) window.localStorage.setItem(LAST_PROJECT_KEY, id);
      else window.localStorage.removeItem(LAST_PROJECT_KEY);
    } catch (e) {}
  }

  function ProjectKanbanApp() {
    // Follow QwenPaw's live language setting; other locales fall back to en.
    var locale = normalizeLocale(useHostLocale());
    function t(key, values) { return tr(locale, key, values); }

    var agentsState = React.useState([]);
    var agents = agentsState[0];
    var setAgents = agentsState[1];
    var projectsState = React.useState(null);
    var projects = projectsState[0];
    var setProjects = projectsState[1];
    var errorState = React.useState(null);
    var loadError = errorState[0];
    var setLoadError = errorState[1];
    var summaryState = React.useState({});
    var summary = summaryState[0];
    var setSummary = summaryState[1];
    var currentState = React.useState(readLastProject);
    var current = currentState[0];
    var setCurrent = currentState[1];
    var createState = React.useState(false);
    var creating = createState[0];
    var setCreating = createState[1];

    function loadProjects() {
      return api.listProjects()
        .then(function (data) {
          setProjects((data && data.projects) || []);
          setLoadError(null);
        })
        .catch(function (err) { setLoadError(errorText(t, err)); });
    }

    function loadSummary() {
      api.approvalSummary().then(function (data) { setSummary((data && data.projects) || {}); }).catch(function () {});
    }

    React.useEffect(function () {
      api.listAgents().then(function (data) { setAgents((data && data.agents) || []); }).catch(function () {});
    }, []);

    // Poll the project list only while the Projects screen is visible.
    React.useEffect(function () {
      loadProjects();
      if (current) return undefined;
      loadSummary();
      var timer = setInterval(function () { loadProjects(); loadSummary(); }, 10000);
      return function () { clearInterval(timer); };
    }, [current, locale]);

    // An unknown or deleted project id falls back to the Projects screen.
    React.useEffect(function () {
      if (current && projects && !projects.some(function (p) { return p.id === current; })) open(null);
    }, [projects, current]);

    function open(id) {
      writeLastProject(id);
      setCurrent(id);
    }

    function createProject(body) {
      return api.createProject(body)
        .then(function (project) {
          message.success(t("projectCreated"));
          setCreating(false);
          loadProjects().then(function () { open(project.id); });
        })
        .catch(function (err) { message.error(errorText(t, err)); });
    }

    var screen = current && projects && projects.some(function (p) { return p.id === current; })
      ? h(Workspace, {
          key: "workspace",
          t: t,
          locale: locale,
          projectId: current,
          projects: projects,
          agents: agents,
          onBack: function () { open(null); },
          onSwitch: open,
          onMissing: function () { open(null); loadProjects(); },
          onChanged: loadProjects,
        })
      : h(ProjectsScreen, {
          key: "projects",
          t: t,
          projects: projects,
          agents: agents,
          approvals: summary,
          error: loadError,
          onOpen: open,
          onRefresh: function () { loadProjects(); loadSummary(); },
          onCreate: function () { setCreating(true); },
        });

    return h(
      "div",
      {
        style: {
          padding: 16, paddingTop: 24, height: "100%", display: "flex", flexDirection: "column",
          background: C.boardBg, color: C.text, boxSizing: "border-box",
        },
      },
      h("style", { dangerouslySetInnerHTML: { __html: CSS } }),
      screen,
      creating
        ? h(ProjectModal, {
            t: t,
            agents: agents,
            takenKeys: (projects || []).map(function (p) { return p.key; }),
            onCancel: function () { setCreating(false); },
            onSubmit: createProject,
          })
        : null,
    );
  }

  // Test seam: backend/tests/test_ui.py loads this file in a sandbox and
  // inspects the pure helpers. Inactive in the console.
  if (window.__PROJECT_KANBAN_TEST__) {
    window.__PROJECT_KANBAN_TEST__.internals = {
      MESSAGES: MESSAGES,
      normalizeLocale: normalizeLocale,
      tr: tr,
      missingKeys: missingKeys,
      renderMarkdown: renderMarkdown,
      createStreamManager: createStreamManager,
      checkMove: checkMove,
      deriveKey: deriveKey,
      errorText: errorText,
      ApiError: ApiError,
      apiFetch: apiFetch,
      api: api,
    };
  }

  // ── 8. Route registration ──────────────────────────────────────────────
  QwenPaw.registerRoutes(APP_ID, [
    {
      path: "/apps/project-kanban",
      component: ProjectKanbanApp,
      label: "Project Kanban",
      icon: "🗂️",
    },
  ]);

  console.info("[project-kanban] registered route /apps/project-kanban");
})();
