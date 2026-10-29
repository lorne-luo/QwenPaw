# Project Kanban — Requirements

| Item | Value |
|------|-------|
| App id | `project-kanban` |
| Status | Draft v1 — requirements only, no implementation yet |
| Date | 2026-10-26 |
| Template | `plugins/apps/agent-kanban` v0.1.1 (read-only reference; must not be modified) |
| Audience | The developer/agent who implements the app, and the reviewer |

This is the single entry document. Everything needed to implement v1 is here; open
items that need a spike are collected in [§18](#18-open-questions-m0-spike-and-risks).

---

## 1. Background and positioning

`agent-kanban` is a flat board: every issue has one global assignee and there is no
grouping above the issue. That works for "send this agent a one-off task", but not for
work that several agents contribute to over time — a website rebuild, a research report,
a data migration — where the issues share context, the same set of agents works on them,
and the owner wants to see that work as one unit.

`project-kanban` makes **Project** the primary dimension:

- A project has a brief (description) and a **member list** of agents.
- Issues belong to exactly one project. Each issue is assigned to one member; different
  issues of the same project can go to different members, so several agents work on the
  same project in parallel.
- The project brief and membership are given to the agent when it runs an issue, and
  agents can query their projects and the project's issues through agent-facing tools.

One-line positioning: **a per-project, multi-agent kanban where several QwenPaw agents
work on the same project in parallel, each through its own queue.**

## 2. Goals and non-goals

### 2.1 Goals (v1)

| ID | Goal |
|----|------|
| G1 | CRUD for projects, including archive / unarchive / delete. |
| G2 | Project membership: add and remove agents with predictable side effects. |
| G3 | Project-scoped issues with the same 5-status board as agent-kanban, human-readable keys (`WEB-12`), and assignees restricted to members. |
| G4 | Execution through the assignee agent (`ctx.chat_stream`, one session per issue), with a dispatcher that enforces **one running issue per agent across all projects** and FIFO queues. |
| G5 | Live output (SSE) and tool-approval handling scoped to the project. |
| G6 | Agent-facing read-only tools: list my projects, list a project's issues. |
| G7 | UI: project list, project board, members view, member management; zh/en i18n that follows the host locale. |

### 2.2 Non-goals (v1)

Listed so the implementer does not build them; candidates for later are in [§17.2](#172-after-v1-candidates).

- Issue dependencies (`blocked_by`), sub-issues, comments, labels, priorities, due dates.
- Agent **write** tools (agents creating or moving issues).
- Per-project concurrency caps or project priorities in the scheduler.
- Multi-user permissions (QwenPaw here is a single-owner deployment).
- Scheduling coordinated with agent-kanban (the two apps do not know about each other).
- Importing agent-kanban data.
- Server push for board state (polling is kept, as in the template).
- Manual reordering of an agent's queue.

## 3. Users and user stories

Actors: **Owner** (the human using the QwenPaw console) and **Member agent** (a QwenPaw
agent added to a project).

| ID | Story | Acceptance |
|----|-------|------------|
| US1 | As the owner, I create a project with a name, a key, a brief, and member agents. | The project appears in the project list with its key, members, and zero counts. |
| US2 | As the owner, I split the project into issues and assign each to a member. | The assignee picker only offers members; non-members are rejected by the API. |
| US3 | As the owner, I queue or run issues; different members work at the same time, and issues for the same agent wait their turn. | Two members run concurrently; a second issue for a busy agent shows "Queued #n" and starts when the agent is free. |
| US4 | As the owner, I watch a running issue's live output and approve or deny the tool calls that need approval. | Live tool / message events appear on the card; approval buttons resolve the request. |
| US5 | As the owner, I review a finished result, then mark it done, run it again, or reopen it. | Successful runs land in *In Review*; results are viewable after the run. |
| US6 | As the owner, I see each member's load in this project, including whether the member is busy in **another** project. | The Members view shows Idle / Running here / Busy in *&lt;project&gt;* and queue length. |
| US7 | As a member agent, I can list the projects I belong to and the issues of one of them, including teammates' result summaries. | Tools return only member projects; non-members get an error. |
| US8 | As the owner, I archive a finished project (read-only, nothing dispatched), unarchive it, or delete it. | Archive is refused while an issue runs; delete is allowed only for archived projects. |
| US9 | As the owner, I add or remove members and know what happens to their issues. | The remove confirmation states how many issues return to Backlog; removal is refused while the member is running an issue in this project. |
| US10 | As the owner, the UI follows my QwenPaw language (zh / en; other locales fall back to en). | Switching the host locale re-renders all labels. |

## 4. Assumptions and glossary

### 4.1 Assumptions (decided while brainstorming; revisit if wrong)

| ID | Assumption |
|----|------------|
| A1 | Single owner per QwenPaw instance; no per-user ACL on projects. |
| A2 | Scale: ≤ 50 active projects, ≤ 20 agents, ≤ 5,000 issues in total. The storage decision relies on this. |
| A3 | Within project-kanban an agent runs at most one issue at a time, across all projects. |
| A4 | agent-kanban and project-kanban may both be installed; they are not coordinated (an agent may run one issue from each at the same time). |
| A5 | A project is not bound to a filesystem directory or repository. If the work needs one, the owner writes it into the project brief. |
| A6 | Every run starts from a cleared session (as in the template), so an issue's run is self-contained; context comes from the prompt and the tools. |

### 4.2 Glossary

| Term | Meaning |
|------|---------|
| Project key | Short uppercase code (`WEB`), unique, immutable; prefix of issue keys. |
| Issue key | `<project key>-<number>`, e.g. `WEB-12`; the number is per project and never reused. |
| Member | An agent id listed in `project.members`. |
| Run | One execution of an issue by its assignee through `ctx.chat_stream`. |
| Queue | Per agent: the `todo` issues assigned to that agent in active projects, ordered by `queued_at`. |
| Dispatcher | Background component that starts the head of each idle agent's queue. |

## 5. Relationship to agent-kanban

### 5.1 What is reused

PawApps are self-contained directories, so reuse means **copying and adapting**, not
importing from `agent-kanban` (importing across plugin directories is fragile and would
couple release cycles). The accepted cost is duplicated code.

| Area | Reused from agent-kanban | Adaptation |
|------|--------------------------|------------|
| Packaging | `plugin.json` shape: `type: app`, `entry.backend` / `entry.frontend`, `meta.pawapp` (`entry_page: /apps/project-kanban`, `launch_scope: page`), `permissions: {chat, storage}`, `description_i18n` | New id, name, icon. |
| Backend framework | `PawApp` + FastAPI `APIRouter` + `Depends(get_ctx)`; `@app.on_launch` / `@app.on_terminate` | Routes nested under `/projects`. |
| Persistence | JSON file under `<app_dir>/data/`, asyncio lock + `fcntl` cross-process lock (`_Txn`), in-memory cache, atomic temp-file + `os.replace` write | Single `board.json` holding projects and issues; write-through instead of a deferred loop ([§7](#7-storage-and-persistence)). |
| Execution | `ctx.chat_stream(prompt, session_id=...)` with `dataclasses.replace(ctx, agent_id=assignee)`; `/clear` before each run; run as a detached `asyncio.Task` so `/stop` can cancel it | Namespaced session id; project-aware prompt. |
| Live stream | `SSEChannel` per issue, in-memory trace replayed on connect, `X-Accel-Buffering: no` | One event mapper shared by live and replay; trace capped. |
| Approvals | Global `ApprovalService` (`get_approval_service`, `approval_display_fields`, `resolve_request` with `ApprovalDecision`) | Exact session match; approve/deny scoped to a project. |
| Session cleanup | Delete the issue's session file via the agent workspace session manager | Uses the agent that actually ran the issue. |
| Frontend runtime | IIFE, `window.QwenPaw.host` React/antd, `React.createElement`, no bundler; `QwenPaw.registerRoutes`; `host.getApiUrl` / `host.getApiToken`; `EventSource` with `?token=` | Same. |
| Frontend UI | Palette, 5 column definitions and colors, `IssueCard`, drag by card header, `AgentLane`/`AgentBoard`, `ViewToggle`, `relTime`, `renderMarkdown` | Cards show issue keys; lanes become project members; markdown renderer hardened. |
| i18n | `MESSAGES = {zh, en}`, `normalizeLocale`, `tr(locale, key, values)` with `{name}` placeholders, `host.useLocale`; issue `language` drives prompt language | More keys; backend error codes mapped to messages. |

### 5.2 What is different

| Topic | agent-kanban | project-kanban |
|-------|--------------|----------------|
| Top-level entity | Issue | **Project** (contains issues and members) |
| Assignee scope | Any agent | Members of the issue's project only |
| Issue display key | `PAW-<uuid>` | `WEB-12` (project key + per-project number) |
| Assigning on Backlog | Auto-promotes to *Queued* and dispatches | Assignment only sets the assignee; queueing is an explicit action (planning a project means assigning many issues before starting any) |
| Failed run | Back to *Queued* → dispatcher retries every 30 s, forever | Back to *Backlog* with the error shown; no auto-retry |
| Dispatch trigger | 30 s polling loop only | Event-driven wake-up (< 1 s) plus a 30 s safety sweep |
| Queue order | Order of the issues array | `queued_at` FIFO per agent, across projects |
| Busy rule | One running issue per agent | Same, applied across **all projects**; UI shows *busy in another project* |
| Moving into *In Progress* | Any PATCH can set `in_progress`; the dispatcher then launches it as an "orphan" | Only `/run` can start a run; `PATCH` to or from `in_progress` is rejected |
| Storage | `issues.json` (array) | `board.json` (`schema_version`, `projects`, `issues`) |
| Persistence | In-memory cache, persisted every 10 s by a loop | Write-through at the end of each transaction |
| Session id | Raw issue UUID | `pawapp:project-kanban:issue:<issue_id>` |
| Approval match | `session_id == id` or `endswith(":" + id)` | Exact match on `session_id` or `root_session_id` |
| Approve / deny | Resolves any pending request id in the system | Only requests that belong to a running issue of the given project |
| Result | Session only | Session plus `last_run.result_excerpt` (≤ 2,000 chars) so tools and cards can show teammates' results cheaply |
| Agent tools | Docstring mentions `list_my_kanban_issues`, but no tool is registered | `project_kanban_list_my_projects`, `project_kanban_list_project_issues` (plus P1 tools) |
| Views | Issue board · Agent view | Project list · Project board · Members view · Members drawer |

### 5.3 Template defects not to copy

Found while reading agent-kanban. They are **not** to be fixed in agent-kanban by this
work (out of scope); project-kanban must not inherit them.

| # | Defect in agent-kanban | Requirement in project-kanban |
|---|------------------------|-------------------------------|
| D1 | `create_issue` / `patch_issue` compute `trigger_agent` but only cache ctx; the issue waits for the 30 s loop. | R-DSP-3: wake the dispatcher on every eligibility change. |
| D2 | A failed run returns to `todo` and is re-dispatched every 30 s indefinitely (token burn on persistent errors). | R-RUN-4: failure → `backlog` + error. |
| D3 | `PATCH status=in_progress` sets the status without running; the dispatcher later launches it as an orphan. | R-SM-2: only `/run` enters `in_progress`. |
| D4 | `/approvals/{id}/approve` and `/deny` resolve any pending request, including ones unrelated to the kanban. | R-APR-3: verify ownership before resolving. |
| D5 | The cache is never reloaded from disk, so the `fcntl` lock does not give cross-process consistency; up to 10 s of writes are lost on a crash. | R-STO-2 / R-STO-3: write-through, reload when the file changed. |
| D6 | `/result` and session deletion use the *current* assignee; after reopen + reassign they look in the wrong workspace. | `last_run.agent_id` is the source of truth. |
| D7 | Live SSE sends only tool events; replay also sends message text, so output differs depending on when the browser connects. | R-SSE-2: one mapper for live and replay. |
| D8 | `renderMarkdown` keeps `"` unescaped and accepts any link scheme, so agent output like `[x](javascript:…)` or a crafted `"` becomes script/attribute injection in the board. | R-SEC-2: escape quotes, allow only `http:`/`https:` links. |
| D9 | The UI throws `HTTP 400`, dropping the server's reason. | R-UI-9: show localized messages from error codes. |
| D10 | `_LIVE_TRACE` grows without bound during long runs. | R-SSE-3: cap the trace. |

## 6. Data model

All timestamps are Unix epoch **seconds** as floats (same as the template; the UI's
`relTime` expects seconds). IDs are UUID4 strings.

### 6.1 Project

| Field | Type | Rules |
|-------|------|-------|
| `id` | str | UUID4, server-generated. |
| `key` | str | `^[A-Z][A-Z0-9]{1,5}$`, unique across all projects (archived included), **immutable**. Optional on create; if omitted the server derives it from the name (uppercase initials of ASCII words, max 4 chars; if none, `P` + an incrementing number) and appends a digit on collision. |
| `name` | str | 1–80 chars after trim. Not required to be unique. |
| `description` | str | 0–4,000 chars. The **project brief**; shown in the UI and injected into every run prompt. |
| `members` | list[Member] | Unique by `agent_id`; may be empty. |
| `archived` | bool | Default `false`. |
| `archived_at` | float \| null | Set on archive, cleared on unarchive. |
| `next_number` | int | Starts at 1; incremented when an issue is created; never decremented. |
| `created_at`, `updated_at` | float | Server-managed. |

Member:

| Field | Type | Rules |
|-------|------|-------|
| `agent_id` | str | A QwenPaw agent id. |
| `added_at` | float | Server-managed. |

No member roles in v1.

### 6.2 Issue

| Field | Type | Rules |
|-------|------|-------|
| `id` | str | UUID4. |
| `project_id` | str | Immutable; issues do not move between projects in v1. |
| `number` | int | From `project.next_number` at creation. Display key = `project.key + "-" + number`. |
| `title` | str | 1–200 chars after trim (the template's silent `"Untitled"` fallback is replaced by a validation error). |
| `description` | str | 0–8,000 chars. |
| `status` | enum | `backlog` \| `todo` \| `in_progress` \| `review` \| `done`. |
| `assignee` | str | `""` or the agent id of a **current member** at write time. |
| `language` | enum | `zh` \| `en`; prompt language. A missing value is treated as `en`; there is no legacy data, unlike the template's `zh` fallback. |
| `queued_at` | float \| null | Set when the issue enters `todo`; cleared when it leaves `todo`. Queue order key. |
| `last_run` | LastRun \| null | Outcome of the most recent run. |
| `created_at`, `updated_at` | float | Server-managed. |

LastRun:

| Field | Type | Rules |
|-------|------|-------|
| `agent_id` | str | Agent that executed the run; owns the session. |
| `session_id` | str | `pawapp:project-kanban:issue:<issue_id>`. |
| `started_at` | float | |
| `finished_at` | float \| null | `null` while running. |
| `outcome` | enum \| null | `succeeded` \| `failed` \| `stopped` \| `interrupted`; `null` while running. |
| `error` | str \| null | Set when `outcome == failed`. |
| `result_excerpt` | str \| null | Text of the last completed assistant message of a successful run, truncated to 2,000 chars with `…`. |

### 6.3 Relationships and invariants

```
Project 1 ──< Member (agent_id)            many agents per project, many projects per agent
Project 1 ──< Issue                        issue.project_id
Issue   * ──1 Agent (assignee, optional)   assignee ∈ project.members when set
```

| ID | Invariant |
|----|-----------|
| I1 | At most one issue with `status == in_progress` per agent, across all projects. |
| I2 | `status == todo` ⇒ `assignee != ""` and `queued_at != null`. |
| I3 | `status == in_progress` ⇒ `last_run.finished_at == null` and `last_run.agent_id == assignee`. |
| I4 | An archived project has no `in_progress` issue. |
| I5 | For `backlog` / `todo` issues, `assignee` is `""` or a current member. `review` / `done` issues may keep a former member as assignee, for history. |
| I6 | `(project_id, number)` is unique. |

### 6.4 Derived data (computed, not stored)

- `project.counts`: number of issues per status.
- Agent dispatch state: running issue and ordered queue per agent (see `GET /dispatch`).
- Queue position of a `todo` issue: 1-based index within its assignee's queue.

## 7. Storage and persistence

### 7.1 File organisation — options

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A. Single file** `data/board.json` | `{schema_version, projects[], issues[]}` | Cross-entity changes (cascade delete, member removal that unassigns issues, the cross-project agent queue) happen in one atomic write under one lock; the closest to the template | The whole file is rewritten on each mutation |
| B. Two files `projects.json` + `issues.json` | Split by entity | Smaller writes for project edits | Cascades need two writes: a crash between them leaves orphans; two locks or one lock over two files |
| C. Per project `data/projects/<id>/{project,issues}.json` | Shard by project | Isolation; scales with many projects | The busy rule and queues span projects, so the dispatcher needs every shard each cycle; many locks |
| D. SQLite | Relational store | Real transactions, cross-process safe, queries | Departs from the template pattern; schema migrations; overkill at A2 scale |

**Decision: A.** At the A2 scale `board.json` stays under ~5 MB; one atomic rewrite per
user action or run transition is cheap. **Revisit** (move to C or D) if the file goes over
5 MB or issue count over 10,000.

### 7.2 Format

```json
{
  "schema_version": 1,
  "projects": [ { "id": "…", "key": "WEB", "name": "Website rebuild", "description": "…",
                  "members": [ { "agent_id": "default", "added_at": 1761400000.0 } ],
                  "archived": false, "archived_at": null, "next_number": 3,
                  "created_at": 1761400000.0, "updated_at": 1761400000.0 } ],
  "issues":   [ { "id": "…", "project_id": "…", "number": 1, "title": "…", "description": "…",
                  "status": "review", "assignee": "default", "language": "zh", "queued_at": null,
                  "last_run": { "agent_id": "default", "session_id": "pawapp:project-kanban:issue:…",
                                "started_at": 1761400100.0, "finished_at": 1761400400.0,
                                "outcome": "succeeded", "error": null, "result_excerpt": "…" },
                  "created_at": 1761400000.0, "updated_at": 1761400400.0 } ]
}
```

### 7.3 Persistence rules

| ID | Rule |
|----|------|
| R-STO-1 | Location `<app_dir>/data/board.json`, lock file `<app_dir>/data/.board.lock`, as in the template (`Path(__file__).resolve().parent.parent / "data"`, adjusted if the backend is split into a package — see [§16](#16-file-layout-and-packaging)). |
| R-STO-2 | Every read-modify-write runs inside a transaction: the asyncio lock **and** an exclusive `fcntl.flock` on the lock file (`flock` called through `asyncio.to_thread`, as in the template). |
| R-STO-3 | At transaction start, after taking the lock, reload the cache if the file's `(st_mtime_ns, st_size)` differs from the last value seen. At transaction end, if anything changed, write atomically (temp file in the same dir + `fsync` + `os.replace`) **before** releasing the lock. *Deviation from the template's 10 s loop, justified by D5; the background persistence loop and the shutdown flush are not needed.* |
| R-STO-4 | Reads outside a transaction (GET routes, tools) use the in-memory cache, refreshed by the same mtime check. |
| R-STO-5 | A missing file means an empty board. An existing file that does not parse is retried 5× (50 ms apart) and then raises; the app must **not** overwrite it with an empty board. |
| R-STO-6 | `schema_version` is checked on load. Unknown higher versions → refuse to start the board (log an error, API returns 503) instead of corrupting data. |
| R-STO-7 | Run state that is not persisted (task handles, SSE channels, live traces) lives in process memory, keyed by issue id, as in the template. |

## 8. State machine and business rules

### 8.1 Statuses

Same 5 columns and labels as the template:

| Status | zh | en | Meaning |
|--------|----|----|---------|
| `backlog` | 待规划 | Backlog | Being planned; not scheduled. |
| `todo` | 等待调度 | Queued | Scheduled; waits in the assignee's queue. |
| `in_progress` | 进行中 | In Progress | Being executed by the assignee. |
| `review` | 审核中 | In Review | Run succeeded; waits for the owner. |
| `done` | 已完成 | Done | Accepted by the owner. |

### 8.2 Transitions

Manual transitions go through `PATCH …/issues/{id}` (`status`), or the action routes where noted.

| From → To | Trigger | Preconditions | Side effects |
|-----------|---------|---------------|--------------|
| `backlog` / `review` / `done` → `todo` | Drag to *Queued*, "Queue" button, or `PATCH` | Assignee set and a member; project active | `queued_at = now`; wake dispatcher |
| `todo` → `backlog` | "Cancel" or drag | — | `queued_at = null` |
| `backlog` / `todo` / `review` → `in_progress` | `POST …/run` (also: drop on the *In Progress* column) | Assignee set and a member; project active | Agent idle → start run. Agent busy → status `todo` (keeping `queued_at` if already queued), response `started: false` |
| `in_progress` → `backlog` | `POST …/stop` | — | Cancel task; `last_run.outcome = stopped`; wake dispatcher |
| `in_progress` → `review` | System: run succeeded | — | `last_run.outcome = succeeded`, `result_excerpt` set; wake dispatcher |
| `in_progress` → `backlog` | System: run failed | — | `last_run.outcome = failed`, `error` set; wake dispatcher |
| `in_progress` → `todo` | System: found orphaned at startup | — | `last_run.outcome = interrupted`; `queued_at = last_run.started_at` (front of the queue) |
| `review` → `done` | "Mark done" or drag | — | — |
| `backlog` / `todo` → `done` | Drag | — | `queued_at = null` (the owner finished it without an agent) |
| `done` → `review` | Drag | — | Kept from the template |
| `review` / `done` → `backlog` | "Reopen" or drag | — | — |

| ID | Rule |
|----|------|
| R-SM-1 | Any transition not in the table is rejected with `409 INVALID_TRANSITION`; the UI checks the same table before an optimistic update and shows a localized warning. |
| R-SM-2 | `PATCH` cannot set `in_progress` and cannot change the status of an `in_progress` issue (`409 ISSUE_RUNNING`). Only `/run` and `/stop` (and the system) do. |
| R-SM-3 | `assignee` can change only in `backlog` or `todo` (`409 ASSIGNEE_LOCKED` otherwise). Changing it on a `todo` issue resets `queued_at = now` (goes to the end of the new agent's queue). Setting `""` on a `todo` issue moves it to `backlog`. |
| R-SM-4 | Assigning on `backlog` does **not** promote to `todo` (difference from the template). |
| R-SM-5 | `title` / `description` / `language` can be edited in any status except `in_progress`. |
| R-SM-6 | Delete is refused for `in_progress` issues (`409 ISSUE_RUNNING`); stop first. Delete removes the issue and, best effort, the session file of `last_run.agent_id`. |
| R-SM-7 | Create accepts `status` `backlog` (default) or `todo`; `todo` requires an assignee who is a member. |

### 8.3 Membership rules

| ID | Rule |
|----|------|
| R-MEM-1 | Adding members is idempotent; existing members are left unchanged. |
| R-MEM-2 | If the host can resolve agent ids from the backend (Q6), unknown ids are rejected with `400 AGENT_NOT_FOUND`; otherwise they are accepted and shown as "Unknown agent (`id`)". |
| R-MEM-3 | Removing a member who has an `in_progress` issue **in this project** is refused with `409 MEMBER_BUSY`. |
| R-MEM-4 | On removal: their `backlog` issues in this project become unassigned; their `todo` issues become unassigned **and** move to `backlog`. `review` / `done` issues keep the assignee (I5). The response reports both counts. |
| R-MEM-5 | A member's work in *other* projects is not affected. |

### 8.4 Project lifecycle

| ID | Rule |
|----|------|
| R-PRJ-1 | Archive is refused while any issue of the project is `in_progress` (`409 PROJECT_HAS_RUNNING`). |
| R-PRJ-2 | An archived project is read-only: creating/editing/deleting/running issues and changing members return `409 PROJECT_ARCHIVED`. Its `todo` issues keep their status but are not dispatched (paused) and do not count in queues. |
| R-PRJ-3 | Unarchive restores normal behaviour; paused `todo` issues re-enter their queues with their original `queued_at`, and the dispatcher is woken. |
| R-PRJ-4 | Delete is allowed only for archived projects (`409 PROJECT_NOT_ARCHIVED`), a two-step guard that also guarantees nothing is running. It removes the project and all its issues in one write, then best-effort deletes their session files. |
| R-PRJ-5 | `name` and `description` can be edited while active; `key` never. |

## 9. Execution and scheduling

### 9.1 Run lifecycle

1. The issue becomes eligible (`todo`, or `/run` was called).
2. Dispatcher or `/run` decides inside a transaction: agent idle (I1) → set `in_progress`,
   create `last_run` (`agent_id`, `session_id`, `started_at`), clear `queued_at`.
3. After the transaction: create a fresh `SSEChannel` for the issue, then start a detached
   task: `ctx' = dataclasses.replace(base_ctx, agent_id=assignee)`; `chat_stream("/clear",
   session_id)`; `chat_stream(prompt, session_id)`.
4. Each completed (non-delta) event is appended to the live trace, mapped (§10.1) and sent
   on the channel.
5. On completion, write the outcome (§8.2) in a transaction, send `done` or `error`, close
   the channel, drop the trace, wake the dispatcher.
6. On cancellation (`/stop`), the stop handler owns the status change; the task re-raises
   `CancelledError` without writing an outcome (same as the template).

### 9.2 Sessions

| ID | Rule |
|----|------|
| R-RUN-1 | Session id = `pawapp:project-kanban:issue:<issue_id>`: one session per issue, stable across runs, cleared at the start of each run. |
| R-RUN-2 | If a new run's agent differs from `last_run.agent_id`, delete the previous agent's session file first (best effort), so only `last_run.agent_id` ever holds a session for the issue. |
| R-RUN-3 | `/result` reads the session history of `last_run.agent_id`, not of the current assignee (D6). |
| R-RUN-4 | A run that raises → `backlog` with `last_run.error`; no automatic retry (D2). |
| R-RUN-5 | If the assignee agent no longer exists when a run starts, the run fails with the error `Agent <id> not found` (same outcome as R-RUN-4). |

### 9.3 Prompt

Built in the issue's `language`. The full templates are in [Appendix A](#appendix-a-prompt-templates). The prompt contains:

- the project name, key, and brief (`description`);
- the member list (agent display names when resolvable, otherwise ids), so the agent knows
  who its teammates are;
- the issue key, title, and description;
- a hint that `project_kanban_list_project_issues` shows the other issues and teammates'
  result excerpts;
- the instruction to finish the issue and report concisely in the issue language.

Sibling issues are **not** inlined into the prompt (it would grow without bound); the
agent pulls them through the tool when needed.

### 9.4 Busy and queueing rules (project dimension)

| ID | Rule |
|----|------|
| R-DSP-1 | **Concurrency unit is the agent.** An agent runs at most one project-kanban issue at a time, across all projects (I1). Different agents run in parallel, whether they are in the same project or not. There is no per-project cap. |
| R-DSP-2 | **Queue** of agent X = issues with `status == todo`, `assignee == X`, project not archived, X still a member; ordered by `queued_at` ascending, then `created_at`, then `id`. This is one global FIFO across X's projects: work is served in the order it was queued, and no project can starve another by having more issues. |
| R-DSP-3 | **Wake-up:** every event that can make an issue eligible or free an agent (issue → `todo`, assignee changed on a `todo` issue, run finished/stopped, unarchive, `base_ctx` captured for the first time after launch) signals the dispatcher (`asyncio.Event`). Target: an eligible issue for an idle agent starts within 1 s. A safety sweep also runs every 30 s. |
| R-DSP-4 | One dispatcher pass, for each agent with a non-empty queue and no running task: take the head; in a transaction re-check that it is still `todo`, eligible, and the agent still idle; flip to `in_progress`; launch after the transaction. Passes are serialised (one pass at a time). |
| R-DSP-5 | `/run` on an issue whose agent is busy puts it in `todo` (keeping an existing `queued_at`, else `now`) and returns `started: false` with `queue_position`. |
| R-DSP-6 | **Orphans:** on launch, every `in_progress` issue without a live task becomes `todo`, `outcome = interrupted`, `queued_at = last_run.started_at`, or just before the agent's oldest queued issue when that one is earlier, so it is first in its agent's queue (the template also gives orphans top priority). |
| R-DSP-7 | **Base ctx:** at `on_launch` the app builds `base_ctx` from the host app state (Q1: possible), so dispatch resumes right after a restart. Each request then refreshes `base_ctx` with its ctx, and `agent_id` is replaced per run (instead of the template's per-agent `_LAST_CTX`). Only if the launch ctx cannot be built is dispatch deferred until the first HTTP request (opening the app unblocks it). *Revised after M0 (Q1).* |
| R-DSP-8 | The UI's queue positions and "busy in another project" come from `GET /dispatch`, not from client-side calculation over one project's issues. |

### 9.5 Stop

`POST …/stop`: cancel the task, close the channel, drop the trace, set `backlog` and
`last_run.outcome = stopped`, `finished_at = now`, wake the dispatcher. Best effort: deny
pending approvals whose session matches the issue, so they do not linger in the global
approval list (confirm the API in M0, Q5).

## 10. Realtime stream and approvals

### 10.1 SSE

| ID | Rule |
|----|------|
| R-SSE-1 | `GET /projects/{pid}/issues/{iid}/stream` returns `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no` (needed behind the nginx on this server). Auth token via `?token=` (EventSource cannot set headers), as in the template. |
| R-SSE-2 | Event payloads (JSON in `data:`): `{"type":"message","text":…}`, `{"type":"tool_start","name":…}`, `{"type":"tool_done","name":…}`, `{"type":"done"}`, `{"type":"error","message":…}`. Live sending and replay use **the same mapper** (D7). The tool name `assistant` is skipped, as in the template. |
| R-SSE-3 | On connect, replay the trace of the current run, then follow the channel. The trace keeps at most the last 1,000 events per issue and is dropped when the run ends. |
| R-SSE-4 | A stream for an issue that is not running sends any replay and then ends; the UI then loads `/result`. |

### 10.2 Result

`GET /projects/{pid}/issues/{iid}/result` keeps the template's response shape so the card
renderer can be reused: `{"messages": [...]}` with the assistant messages from the session
of `last_run.agent_id`, falling back to the live trace while running; `{"error": "…"}` when
the last run failed.

### 10.3 Approvals

| ID | Rule |
|----|------|
| R-APR-1 | `GET /projects/{pid}/approvals` returns `{"approvals": {issue_id: [approval…]}}` for that project's `in_progress` issues. A pending request belongs to an issue when its `session_id` or `root_session_id` equals the issue's `last_run.session_id` (exact match). Approval fields as in the template (`request_id`, `session_id`, `root_session_id`, `tool_name`, `agent_id`, `severity`, `approval_display_fields(p)`, `created_at`). |
| R-APR-2 | `POST /projects/{pid}/approvals/{request_id}/approve` and `/deny` resolve through `ApprovalService.resolve_request`. |
| R-APR-3 | Before resolving, verify that the request is pending and belongs to an `in_progress` issue of `pid`; otherwise `404 APPROVAL_NOT_FOUND` (D4). |
| R-APR-4 | If the approval modules cannot be imported: list returns `{}`, approve/deny return `501 APPROVAL_UNAVAILABLE` (template behaviour). |
| R-APR-5 | (P1) `GET /approvals/summary` → `{"projects": {pid: pending_count}}` for the project list badges. |
| R-APR-6 | The template reads `svc._pending` under `svc._lock` (private API). Keep that only if no public listing exists; isolate it in one function so a host change breaks one place. |

## 11. HTTP API

### 11.1 Conventions

- Mounted under the app prefix exactly as the template (`host.getApiUrl("/project-kanban/…")`).
- JSON bodies; response bodies are bare objects or `{"<plural>": [...]}` lists, matching the
  template (no envelope).
- Errors use FastAPI `HTTPException` with a structured detail:
  `{"detail": {"code": "ASSIGNEE_NOT_MEMBER", "message": "English text for logs"}}`.
  The UI translates `code`; `message` is never shown as the primary text. Pydantic schema
  errors stay as FastAPI's default 422.
- Path ids are plain UUIDs (no `{id:path}` converter). Every issue route checks that the
  issue belongs to `{pid}`, else `404 ISSUE_NOT_FOUND`.
- Mutating routes on an archived project return `409 PROJECT_ARCHIVED` (R-PRJ-2).

### 11.2 Routes

Projects:

| Method | Path | Body / query | Response | Errors |
|--------|------|--------------|----------|--------|
| GET | `/projects` | `?archived=false` (default) \| `true` \| `all` | `{"projects": [Project + counts]}` | — |
| POST | `/projects` | `{name, key?, description?, members?: [agent_id]}` | `201` Project | `400 VALIDATION_ERROR`, `409 PROJECT_KEY_TAKEN`, `400 AGENT_NOT_FOUND` |
| GET | `/projects/{pid}` | — | Project + counts | `404 PROJECT_NOT_FOUND` |
| PATCH | `/projects/{pid}` | `{name?, description?}` | Project | `400`, `404`, `409 PROJECT_ARCHIVED` |
| POST | `/projects/{pid}/archive` | — | Project | `404`, `409 PROJECT_HAS_RUNNING` |
| POST | `/projects/{pid}/unarchive` | — | Project | `404` |
| DELETE | `/projects/{pid}` | — | `{"ok": true, "deleted_issues": n}` | `404`, `409 PROJECT_NOT_ARCHIVED` |

Members:

| Method | Path | Body | Response | Errors |
|--------|------|------|----------|--------|
| POST | `/projects/{pid}/members` | `{agent_ids: [str]}` | Project | `404`, `409 PROJECT_ARCHIVED`, `400 AGENT_NOT_FOUND` |
| DELETE | `/projects/{pid}/members/{agent_id}` | — | `{"project": Project, "unassigned": n, "dequeued": m}` | `404 MEMBER_NOT_FOUND`, `409 MEMBER_BUSY`, `409 PROJECT_ARCHIVED` |

Issues:

| Method | Path | Body / query | Response | Errors |
|--------|------|--------------|----------|--------|
| GET | `/projects/{pid}/issues` | `?status=&assignee=` | `{"issues": [Issue + key]}` | `404` |
| POST | `/projects/{pid}/issues` | `{title, description?, assignee?, status?: backlog\|todo, language?}` | `201` Issue | `400 VALIDATION_ERROR`, `400 ASSIGNEE_NOT_MEMBER`, `400 ASSIGNEE_REQUIRED`, `409 PROJECT_ARCHIVED` |
| PATCH | `/projects/{pid}/issues/{iid}` | `{title?, description?, assignee?, status?, language?}` | Issue | `400`, `404`, `409 INVALID_TRANSITION`, `409 ISSUE_RUNNING`, `409 ASSIGNEE_LOCKED` |
| DELETE | `/projects/{pid}/issues/{iid}` | — | `{"ok": true}` | `404`, `409 ISSUE_RUNNING` |
| POST | `/projects/{pid}/issues/{iid}/run` | `?language=zh\|en` | `{"issue": Issue, "started": bool, "queue_position": int\|null}` | `400 ASSIGNEE_REQUIRED`, `400 ASSIGNEE_NOT_MEMBER`, `409 INVALID_TRANSITION` (e.g. from `done`), `409 PROJECT_ARCHIVED` |
| POST | `/projects/{pid}/issues/{iid}/stop` | — | Issue | `404` (stopping a non-running issue is a no-op that returns the issue) |
| GET | `/projects/{pid}/issues/{iid}/stream` | `?token=` | SSE (§10.1) | `404` |
| GET | `/projects/{pid}/issues/{iid}/result` | — | `{"messages": [...]}` \| `{"error": "…"}` | `404` |

Approvals and runtime:

| Method | Path | Response | Errors |
|--------|------|----------|--------|
| GET | `/projects/{pid}/approvals` | `{"approvals": {issue_id: [Approval]}}` | `404` |
| POST | `/projects/{pid}/approvals/{rid}/approve` | `{"ok": true, "tool_name": …}` | `404 APPROVAL_NOT_FOUND`, `501 APPROVAL_UNAVAILABLE` |
| POST | `/projects/{pid}/approvals/{rid}/deny` | same | same |
| GET | `/approvals/summary` (P1) | `{"projects": {pid: count}}` | — |
| GET | `/dispatch` | `{"agents": {agent_id: {"running": IssueRef\|null, "queue": [IssueRef]}}}` with `IssueRef = {project_id, project_key, project_name, issue_id, key, title}` | — |

The agent list comes from the host's existing `GET /agents` (as in the template); the app
does not proxy it.

### 11.3 Error code catalog

| Code | HTTP | Meaning |
|------|------|---------|
| `VALIDATION_ERROR` | 400 | A field violates §6 limits (the message names the field). |
| `PROJECT_NOT_FOUND` / `ISSUE_NOT_FOUND` / `MEMBER_NOT_FOUND` / `APPROVAL_NOT_FOUND` | 404 | Unknown, or does not belong to the parent in the path. |
| `AGENT_NOT_FOUND` | 400 | Agent id not known to the host (only when Q6 allows the check). |
| `PROJECT_KEY_TAKEN` | 409 | Explicit key already used. |
| `PROJECT_ARCHIVED` | 409 | Mutation on an archived project. |
| `PROJECT_NOT_ARCHIVED` | 409 | Delete before archive. |
| `PROJECT_HAS_RUNNING` | 409 | Archive while an issue runs. |
| `MEMBER_BUSY` | 409 | Removing a member who is running an issue in this project. |
| `ASSIGNEE_REQUIRED` | 400 | Queue/run/create-in-`todo` without an assignee. |
| `ASSIGNEE_NOT_MEMBER` | 400 | Assignee is not a member of the project. |
| `ASSIGNEE_LOCKED` | 409 | Changing the assignee outside `backlog` / `todo`. |
| `INVALID_TRANSITION` | 409 | Status change not in §8.2. |
| `ISSUE_RUNNING` | 409 | Edit/delete/status change on an `in_progress` issue. |
| `APPROVAL_UNAVAILABLE` | 501 | Approval service not importable. |
| `BOARD_UNAVAILABLE` | 503 | `board.json` unreadable or newer `schema_version` (R-STO-5/6). |

## 12. Agent-facing tools

Registered with `@app.tool(...)` (PawApp default `enabled=True`). Names are prefixed
`project_kanban_` because tool names share one global namespace. The caller is identified
with `qwenpaw.app.agent_context.get_current_agent_id()`. All tools are **read-only** in v1.

| Priority | Tool | Parameters | Returns |
|----------|------|------------|---------|
| P0 | `project_kanban_list_my_projects` | `include_archived: bool = False` | Projects where the caller is a member: `id`, `key`, `name`, `description` (first 500 chars), `members` (ids), `counts`, `my_open_issues` (caller's issues not `done`). |
| P0 | `project_kanban_list_project_issues` | `project: str` (id **or** key), `status: str = ""`, `assignee: str = ""`, `limit: int = 50` (max 200) | Issues of that project, newest `updated_at` first: `id`, `key`, `title`, `status`, `assignee`, `updated_at`, `result_excerpt` (first 500 chars). |
| P1 | `project_kanban_get_issue` | `issue: str` (id or key such as `WEB-12`) | Full `description`, `status`, `assignee`, `last_run` incl. the full `result_excerpt`. |
| P1 | `project_kanban_list_my_issues` | `status: str = ""` | The caller's issues across all its projects (covers what agent-kanban's docstring promised as `list_my_kanban_issues`). |

| ID | Rule |
|----|------|
| R-TOOL-1 | If the caller is not a member of the requested project, return `{"error": "NOT_A_MEMBER", "message": …}`; do not reveal whether the project exists. |
| R-TOOL-2 | If the caller id cannot be resolved, return `{"error": "UNKNOWN_CALLER", …}`. |
| R-TOOL-3 | Results are JSON-serialisable dicts, English field names, no localized text. Descriptions are written for the model (what it returns, when to use it). |
| R-TOOL-4 | Tools read the in-memory cache (R-STO-4) and never take the write lock. |
| R-TOOL-5 | `tool_type`: pick the value the PawApp taxonomy uses for local read-only tools (Q7); do not copy `network` blindly from qwenpaw-data. |

## 13. UI

### 13.1 Structure

- One route `/apps/project-kanban` registered with `QwenPaw.registerRoutes("project-kanban", …)`
  (PawApp routes under `/apps/` get no sidebar entry; the App Center opens them).
- Two top-level screens inside the component: **Projects** and **Project workspace**.
  The selected project is kept in the URL query `?project=<id>` if the host router keeps
  query strings on this route (Q2), otherwise in `localStorage` key
  `project-kanban:lastProject`. An unknown or deleted id falls back to the Projects screen.
- Visual language reuses the template (palette `C`, column colors, card style, lane style)
  so the two apps look like siblings.

```
Projects                                              [+ New project]
[Active | Archived]
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ WEB  Website     │ │ RPT  Q4 report   │ │ MIG  DB migrate  │
│ brief (2 lines)  │ │ …                │ │ …                │
│ 🤖🤖🤖 +2        │ │ 🤖🤖             │ │ 🤖               │
│ ▮▮▮▯▯ 3 running  │ │ ⚠ 1 approval     │ │ idle             │
└──────────────────┘ └──────────────────┘ └──────────────────┘

← Projects  [WEB · Website rebuild ▾]  🤖🤖🤖 [Members]  [Board | Members]  ● 2 running  [Refresh] [⋯]
┌ Backlog + ┐┌ Queued ┐┌ In Progress ┐┌ In Review ┐┌ Done ┐
│ WEB-4     ││ WEB-2  ││ WEB-1       ││ WEB-3     ││      │
│ …         ││ #1     ││ ⚠ approval  ││           ││      │
└───────────┘└────────┘└─────────────┘└───────────┘└──────┘
```

### 13.2 Screens and interactions

| ID | Screen / element | Requirements |
|----|------------------|--------------|
| R-UI-1 | **Projects** | Card grid of projects (Active / Archived tabs): key badge, name, brief (2-line clamp), member avatar stack (max 5, then `+n`), per-status counts as a small bar, running count, pending-approval badge (P1). Click opens the workspace. Empty state with a "Create your first project" call to action. Poll `/projects` every 10 s while visible. |
| R-UI-2 | **Project modal** (create / edit) | Fields: name; key (create only, pre-filled from the name, validated against `^[A-Z][A-Z0-9]{1,5}$`, read-only on edit); brief textarea with the hint "Shared with agents as project context"; members multi-select (create only; later changes via the Members drawer). |
| R-UI-3 | **Workspace header** | Back to Projects; project switcher (active projects); key + name; member avatars + "Members" button; view toggle *Board \| Members*; running count; Refresh; `⋯` menu with Edit project, Archive / Unarchive, Delete (enabled only when archived, with a confirmation that names the issue count). |
| R-UI-4 | **Board view** | The template's 5 columns and drag behaviour (drag by card header). `+` on Backlog opens the issue modal: title, description, assignee (**members only**), and a "Queue now" checkbox (enabled when an assignee is chosen; creates with `status: todo`). Drops are checked against §8.2 before the optimistic update; a drop on *In Progress* calls `/run`. |
| R-UI-5 | **Issue card** | Header: status dot + `WEB-12` (drag handle) + one state hint, first match wins: *Awaiting approval* → *Running…* → *Queued #n* (position in the assignee's cross-project queue; when the assignee is running an issue of another project, the hint reads *Queued #n · &lt;agent&gt; busy in &lt;project&gt;*). Title, description (3-line clamp, expand). Assignee select limited to members, disabled outside Backlog/Queued; a former member shows as "&lt;name&gt; (left)". Failed last run shows an error badge whose text expands. Result section and approval panel as in the template. Actions by status: Backlog → *Run* / *Queue* (label depends on agent busy), Edit, Delete; Queued → *Cancel*; In Progress → *Running* + *Stop* (no Delete); In Review → *Run again*, *Mark done*; Done → *Reopen*. |
| R-UI-6 | **Members view** | One lane per member (template `AgentLane`) with that member's issues **in this project**, bucketed by status. Lane badge: *Idle*, *Running here (WEB-1)*, or *Busy in &lt;project&gt;* (from `/dispatch`), plus queue length across all projects. An *Unassigned* lane collects issues without an assignee. Summary chips: busy / idle member counts. |
| R-UI-7 | **Members drawer** | List: avatar, name, id, current state, open-issue count in this project. "Add members": multi-select over host agents not yet members. Remove: `Popconfirm` that states "N queued issues will move back to Backlog; M backlog issues will be unassigned"; disabled with a tooltip when the member is running an issue here. |
| R-UI-8 | **Archived project** | Read-only banner with *Unarchive*; every mutating control disabled; board still browsable and results viewable. |
| R-UI-9 | **Errors** | `apiFetch` parses `detail.code` and shows `t("err." + code)`, falling back to a generic localized message with the HTTP status (D9). |
| R-UI-10 | **Polling** | While a workspace is open: `/projects/{pid}/issues` and `/dispatch` every 4 s (one timer), `/projects/{pid}/approvals` every 3 s. Stop all timers and close all EventSources on unmount and on project switch. |
| R-UI-11 | **Live output** | Expanding the result of a running card subscribes to its SSE stream (template behaviour); `done` / `error` closes the stream and reloads the board. |
| R-UI-12 | **Agent names** | From host `GET /agents`; unknown ids render as the raw id. |

## 14. i18n

| ID | Rule |
|----|------|
| R-I18N-1 | UI follows `host.useLocale()`; `normalizeLocale` maps `zh*` → `zh`, everything else → `en` (host fallback policy, as in the template). Changing the host locale re-renders without reload. |
| R-I18N-2 | All UI strings live in `MESSAGES.zh` / `MESSAGES.en` and go through `tr(locale, key, values)` with `{name}` placeholders. Missing `zh` keys fall back to `en`, then to the key. Both dictionaries must have the same key set (checked in review). |
| R-I18N-3 | Status labels reuse the template's translations (§8.1). Error codes map to `err.<CODE>` keys. |
| R-I18N-4 | Backend returns no user-facing localized text. The only localized backend output is the **prompt**, built in `issue.language` (set from the UI locale on create, and updated by `/run?language=`, as in the template). |
| R-I18N-5 | Agent tools return language-neutral data. |
| R-I18N-6 | User content (project name, brief, issue text) and agent output are shown as entered; project keys are ASCII uppercase in every locale. |
| R-I18N-7 | `plugin.json` provides `description_i18n` for `zh-CN` and `en-US`. |
| R-I18N-8 | Relative times use the localized `relTime` from the template. |

## 15. Non-functional requirements

### 15.1 Security

| ID | Requirement |
|----|-------------|
| R-SEC-1 | All inputs validated against §6 limits at the API boundary. |
| R-SEC-2 | Agent output and user text are rendered with the hardened `renderMarkdown`: escape `& < > " '` before any markup transform; links only for `http:` / `https:` URLs; everything else as plain text (D8). |
| R-SEC-3 | Approve/deny only for requests owned by the project's running issues (R-APR-3). |
| R-SEC-4 | The SSE token travels in the query string (EventSource limitation inherited from the template). On this server nginx may write it to access logs; the README must state this, and the nginx config for the console should not log query strings for `/stream` paths, or the risk is accepted explicitly. |
| R-SEC-5 | Tools enforce membership (R-TOOL-1). |

### 15.2 Performance and limits

- Dispatch latency for an idle agent < 1 s (R-DSP-3).
- One `board.json` write per mutation; GET routes and tools never write.
- Trace ≤ 1,000 events per running issue; result excerpt ≤ 2,000 chars.
- UI polling as in R-UI-10; nothing polls when the app page is closed.

### 15.3 Observability

Log prefix `[project-kanban]`, with `project_id`, `issue_id` / issue key and `agent_id` on
every run, dispatch, stop, and error line. Log levels: `ERROR` for failed runs and storage
errors, `WARNING` for best-effort cleanup failures, `INFO` for run start/finish and
dispatch decisions, `DEBUG` for stream events.

### 15.4 Testing

agent-kanban has no tests; project-kanban must.

| Layer | Scope | Tooling |
|-------|-------|---------|
| Backend unit | Transition table (every allowed and rejected pair, table-driven); membership side effects (R-MEM-3/4); project lifecycle (R-PRJ-*); key derivation and collisions; validation limits | pytest, `backend/tests/` |
| Backend scheduling | One running issue per agent across projects; FIFO across projects by `queued_at`; archived projects paused and resumed; orphan recovery order; `/run` on a busy agent; wake-up latency | pytest-asyncio with a fake `ctx` whose `chat_stream` is a scripted async generator |
| Backend storage | Atomic write (no partial file on simulated failure); reload on external change; unreadable file not overwritten; `schema_version` guard | pytest with `tmp_path` |
| Backend approvals/tools | Exact session match; foreign request rejected; tool membership ACL; key-or-id lookup | pytest with a fake ApprovalService and patched `get_current_agent_id` |
| UI | Manual checklist in the README (no test harness exists for runtime-loaded plugin modules): create project, add members, create/queue/run, two members in parallel, queueing across projects, stop, approve/deny, archive/unarchive/delete, locale switch | Manual |

Target ≥ 80% line coverage on backend non-route modules.

## 16. File layout and packaging

```
plugins/apps/project-kanban/
├── plugin.json
├── README.md                  # same style and language as agent-kanban's README
├── docs/
│   └── requirements.md        # this file
├── backend/
│   ├── main.py                # PawApp wiring: routers, tools, on_launch / on_terminate
│   ├── project_kanban_core/   # unique package name (see note)
│   │   ├── models.py          # request schemas, validation limits
│   │   ├── store.py           # board.json load / transaction / atomic write
│   │   ├── rules.py           # state machine, membership, lifecycle rules
│   │   ├── dispatcher.py      # queues, wake-up, orphan recovery
│   │   ├── runner.py          # run task, event mapping, SSE channels, trace
│   │   ├── approvals.py
│   │   ├── tools.py
│   │   └── routes.py
│   └── tests/
└── ui/
    └── index.js               # single IIFE (no bundler), organised in sections
```

- The template is one 1,341-line `main.py`; project-kanban has roughly twice the surface,
  so the backend is split into focused modules.
- qwenpaw-creator imports its backend modules by putting `backend/` on `sys.path`. Module
  names on `sys.path` are process-global, so a generic name such as `store` could collide
  with another plugin's; hence the unique package name. Q3 confirms how PluginLoader loads
  `main.py` (if relative imports work, use them instead).
- The UI stays one file because the host loads a single module; keep it in clearly
  separated sections (i18n, API, markdown, components, screens, route registration).
- `plugin.json`: `id: project-kanban`, `name: Project Kanban`, `type: app`,
  `entry: {backend: backend/main.py, frontend: ui/index.js}`, `qwenpaw_version.min: 2.0.1`,
  `meta.pawapp: {icon: "🗂️", category: productivity, entry_page: /apps/project-kanban,
  launch_scope: page}`, `meta.permissions: {chat: true, storage: true}`, `settings: []`.

## 17. Milestones

### 17.1 v1

| Milestone | Scope | Exit criteria |
|-----------|-------|---------------|
| **M0 — Spike** (½ day) | Answer Q1–Q8 below with small experiments; record the answers in §18. | Every open question has a decision; any requirement change is made in this document. |
| **M1 — Backend core** | `store.py`, models, projects / members / issues routes (all routes in §11.2 except run, stop, stream, result, approvals, dispatch), state machine and rules, error catalog, `plugin.json` skeleton. | Tests for §8 and storage pass; API can be exercised with curl; `board.json` survives a kill mid-write. |
| **M2 — Execution** | Runner, `/run`, `/stop`, dispatcher with wake-up + sweep, orphan recovery, `/stream`, `/result`, approvals routes, `/dispatch`. | Scheduling tests pass; on a real instance two members run in parallel, a third issue for a busy agent queues and starts automatically, a failing run lands in Backlog with its error, approvals resolve from the API. |
| **M3 — UI** | Projects screen, project modal, workspace board, issue card, Members drawer, archive read-only mode, zh/en dictionaries, error-code mapping, hardened markdown. Can start once the M1 API is frozen. | Manual checklist (§15.4) passes in both locales. |
| **M4 — Agent surface and polish** | P0 tools (+ P1 if time), Members view with cross-project busy state, approval summary badges (P1), README. | Tool ACL tests pass; an agent asked in chat "which projects am I in?" answers from the tool; README covers usage and the SSE token note. |

### 17.2 After v1 (candidates)

Issue dependencies with dispatch gating; agent write tools (create sub-issue, comment,
move to review) behind approval; per-project concurrency caps or round-robin fairness;
server push for board state; queue reordering; importing agent-kanban issues into a project;
a project working-directory field.

## 18. Open questions (M0 spike) and risks

### 18.1 Open questions

| ID | Question | Default if unresolved |
|----|----------|-----------------------|
| Q1 | Can a `PawAppContext` be built at `on_launch` (no request), so the dispatcher can resume right after a restart? | R-DSP-7: wait for the first HTTP request. |
| Q2 | Does the host route for `/apps/project-kanban` keep a query string (deep link `?project=`)? Are path params supported? | `localStorage` for the last project. |
| Q3 | How does PluginLoader import `backend/main.py` (package with relative imports, or a plain module)? | `sys.path` insert + unique package name, as qwenpaw-creator does. |
| Q4 | Can several backend processes serve the same app directory? | Assume yes; R-STO-3 already covers it. |
| Q5 | Do pending approvals carry the namespaced session id in `session_id` or `root_session_id`? Is there an API to cancel a session's pending approvals on stop? Does a `pawapp:project-kanban:…` session show up in the console chat list? | Exact match on either field; skip approval cleanup on stop; accept chat-list visibility if it happens. |
| Q6 | Can the backend check that an agent id exists (workspace registry)? | Accept unknown ids (R-MEM-2 fallback). |
| Q7 | Which `tool_type` fits local read-only tools? | Whatever the registry treats as non-network read-only; document the choice. |
| Q8 | Use `get_ctx` (template) or the stricter `get_scoped_ctx` for these routes? | `get_ctx`, same as the template. |

Answers (M0 spike, 2026-10-26; paths relative to `src/qwenpaw/` unless they start with `console/`):

| ID | Answer | Evidence |
|----|--------|----------|
| Q1 | **Yes.** Startup hooks (`on_launch`) are called with no arguments after the host has set `app.state.multi_agent_manager`, `app_services` and `plugin_registry`; the plugin registry keeps the FastAPI app, and `PawAppContext` is a plain dataclass. The app builds `base_ctx` at launch (`PawApp._plugin_api._registry.get_plugin_http_app().state`) and dispatch resumes right after a restart; the first-request capture stays as a fallback. Hot install runs the same hooks. R-DSP-7 revised accordingly. | `app/_app.py:425` (`multi_agent_manager`), `app/_app.py:309` (`app_services`), `app/_app.py:595` (`plugin_registry`), `app/_app.py:640` (`hook.callback()`), `app/_app.py:514` + `plugins/registry.py:221` (`get_plugin_http_app`), `pawapp/app.py:767` (`_plugin_api`), `plugins/api.py:345` (`_registry`), `pawapp/context.py:187` (`@dataclass PawAppContext`), `app/routers/plugins.py:212` (hot install) |
| Q2 | **No query string, no path params.** `/apps/*` plugin routes are not mounted in the router; the App Center (`/apps/:appId`) looks the plugin route up by exact path and renders its component inline. Opening an app pushes the bare `entry_page` (in OS mode only a history-state id), so `?project=` is not produced or kept. Use `localStorage` key `project-kanban:lastProject`. | `console/src/layouts/MainLayout/index.tsx:49`, `console/src/layouts/registry/builtinRoutes.tsx:371`, `console/src/pages/AppCenter/index.tsx:197`, `:214-222`, `:314` |
| Q3 | **Package with relative imports.** `main.py` is executed as package `plugin_project_kanban` with `__path__ = [backend/, <app_dir>]`, so `from .project_kanban_core import …` works; bare imports are also redirected into the plugin namespace. Decision: relative imports; the unique package name is kept (tests import it via `sys.path`). | `plugins/loader.py:535`, `:544`, `:548`, `:574-576`, `plugins/module_isolation.py:297` |
| Q4 | **One process per `qwenpaw app`** (workers forced to 1). Two instances started with the same `WORKING_DIR` would share `plugins/project-kanban/data/`; R-STO-2/3 keep `board.json` consistent and the busy rule reads the board, but each process recovers "orphans" at launch, so a second process would interrupt the first one's runs. Treated as unsupported; documented in the README. | `cli/app_cmd.py:177`, `constant.py:284` |
| Q5 | `session_id` is the request session (`AgentRequest.session_id` = our namespaced id); `root_session_id` defaults to `session_id`, and sub-agent approvals carry the child session with our id as root → match **either field exactly**. Public API exists: `list_pending_by_session`, `get_pending_by_root_session` (so R-APR-6's private `_pending` access is not needed) and `cancel_all_pending_by_root_session`, used on stop. `ctx.chat_stream` goes straight to `Workspace.stream_query` and creates **no** chat entry (chat specs are created only by channels, console routes and crons), so the session does not appear in the console chat list. | `pawapp/context.py:604`, `governance/tool_adapter.py:595`, `runtime/tool_guard.py:351`, `hooks/request_setup/contextvars_hook.py:82`, `app/approvals/service.py:451`, `:473`, `:608`, `app/workspace/workspace.py:414`, `app/channels/base.py:543`, `app/routers/console.py:455`, `app/crons/executor.py:216` |
| Q6 | **Yes.** Configured agents are the keys of `load_config().agents.profiles` (the same source as `GET /agents`); display names come from `load_agent_config(id).name`. Unknown ids → `400 AGENT_NOT_FOUND`; if the config cannot be read the id is accepted. | `config/utils.py:607`, `app/routers/agents.py:473`, `:483`, `:533` |
| Q7 | **`internal`.** Governance types are `file / network / shell / internal`; `internal` is allowed without deep scan, and the host registers its local read-only search (`memory_search`) as `internal`. PawApp's default `network` is not used. | `governance/tool_registry.py:254`, `:451-454`, `governance/policy.py:722`, `pawapp/app.py:441` |
| Q8 | **`get_ctx`.** `get_scoped_ctx` binds `user_id` to the auth principal and rejects caller claims; the board has no per-user data (A1). Session files are keyed by `user_id`/`channel`, so runs, `/result` and session cleanup use a pinned identity (`default` / `console`) instead of whatever the request carried. | `pawapp/deps.py:106-123`, `:139-160`, `app/chats/session.py:220` |

### 18.2 Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Data lives in the app install directory (template choice); an app reinstall or upgrade may replace the directory. | Board lost | Confirm upgrade behaviour in M0; if the directory is replaced, move `data/` to a stable app-data path provided by the host. |
| Both kanban apps installed: the same agent may run two issues at once. | Load and confusion | Documented (A4); revisit if it matters in practice. |
| Private `ApprovalService._pending` access (inherited). | Breaks on host refactor | Isolated in one function (R-APR-6). |
| Single JSON file grows with done issues. | Slower writes | Revisit threshold in §7.1. |
| Owner removes all members of a project with queued work. | Work silently stalls | R-MEM-4 moves it to Backlog and the confirmation states the counts. |

---

## Appendix A. Prompt templates

English (`language = en`):

```
You are a member agent of the project "{project_name}" ({project_key}).

Project brief:
{project_description or "(No brief)"}

Project members: {member_list}

You have been assigned issue {issue_key}:
Title: {issue_title}
Description: {issue_description or "(No description)"}

To see the other issues of this project and your teammates' results, call
project_kanban_list_project_issues with project="{project_key}".

Complete the issue and report the result concisely in English.
```

Chinese (`language = zh`):

```
你是项目「{project_name}」({project_key}) 的成员智能体。

项目简介:
{project_description 或 "(无简介)"}

项目成员: {member_list}

你被指派处理任务 {issue_key}:
标题: {issue_title}
描述: {issue_description 或 "(无描述)"}

如需了解本项目的其他任务及队友的处理结果，可调用
project_kanban_list_project_issues，参数 project="{project_key}"。

请完成该任务，并用简洁的中文汇报你的处理结果。
```

## Appendix B. Decision log

| # | Decision | Alternatives considered | Reason |
|---|----------|-------------------------|--------|
| 1 | Single `board.json` | Two files, per-project shards, SQLite | Atomic cross-entity changes; closest to the template; enough for A2 scale |
| 2 | Write-through per transaction | Template's 10 s deferred loop | The deferred loop loses writes on crash and defeats the cross-process lock (D5) |
| 3 | Agent is the concurrency unit, global across projects | Per-project slots | An agent's workspace and model budget are shared across projects; matches the template's busy rule |
| 4 | Global FIFO by `queued_at` per agent | Round-robin across projects, project priority | Predictable and simple; fairness options listed for later |
| 5 | Assignment does not auto-queue | Template auto-promote | Project planning assigns many issues before starting any |
| 6 | Failure → Backlog, no auto-retry | Template retry loop, bounded retries | Avoids unbounded token use; the owner decides |
| 7 | Store `result_excerpt` on the issue | Session-only (template) | Teammates (via tools) and cards read results without loading another agent's session |
| 8 | Namespaced session id | Raw issue id | Unambiguous approval matching; clearly owned by the app |
| 9 | Delete only archived projects | Delete anytime with cascade stop | Two-step guard against accidental loss; nothing running at delete time |
| 10 | Read-only agent tools in v1 | Write tools | Write tools need loop protection and approval design |
