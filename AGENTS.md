# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## Fork

This is Lorne's personal fork of [agentscope-ai/QwenPaw](https://github.com/agentscope-ai/QwenPaw).

- Branch naming: `{upstream version}-lorne`, e.g. `v0.5.59-lorne` — the upstream release the branch is
  based on, plus `-lorne`.
- Push only to the remote that belongs to GitHub user `lorne-luo` (`fork` →
  `https://github.com/lorne-luo/QwenPaw.git`). Never push to `origin` (upstream `agentscope-ai/QwenPaw`).

## What this is

QwenPaw is a self-hosted personal AI assistant: a Python/FastAPI backend (`src/qwenpaw/`) that runs
agents, plus a React/Vite web Console (`console/`) that the backend serves as static files. It reaches
users through ~15 chat channels (DingTalk, Feishu, Discord, Telegram, …), a CLI, and a Textual TUI.
Built on `agentscope==2.0.4.post1`.

## Commands

### Backend (Python 3.11–3.13)

```bash
pip install -e ".[dev,test,full]"   # full = local,whisper,codex,qoder,claude extras
pytest                              # all tests
make quick                          # fast unit loop: -x -q with an isolated temp working dir
make test-unit / test-contract / test-integration
make coverage-full                  # coverage gate is fail_under=50 (pyproject)
pytest tests/unit/harnesses/test_codex_adapter.py::test_name -v   # single test
pytest -m "p0"                      # markers: p0/p1/p2, unit/contract/integration/e2e, slow, manual_real
```

`asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`.

### Console (Node 20)

```bash
cd console
npm ci
npm run dev          # :5173, proxies /api -> http://localhost:8088
npm run build        # tsc -b && vite build && verify:monaco-css
npm run test:run     # vitest (raised heap; the plain `vitest` script watches)
npm run format       # tsc -b --noEmit && prettier --write
npm run lint
```

**Type-checking must use `tsc -b`.** The root `tsconfig.json` is a solution file with `files: []` and
project references; `tsc -p tsconfig.json` checks zero files and exits 0. Vite needs
`NODE_OPTIONS=--max-old-space-size=4096` — the default heap is exhausted by this codebase.

### Local dev loop on this box

`./run.sh` builds the console and restarts the `qwenpaw` systemd unit (`127.0.0.1:18888`), timing each
stage. The backend serves `console/dist`, so a backend-only restart leaves frontend changes invisible —
always build. `--no-build` / `--no-restart` skip stages.

**Ask the user before every build or restart** — `./run.sh`, `npm run build`, `systemctl restart qwenpaw`.
The build takes ~4 min, and a restart takes the live assistant offline until the health check passes
(~80 s).

Elsewhere: `qwenpaw init --defaults && qwenpaw app` (defaults to `:8088`).

### E2E

Playwright + pytest under `e2e/`. Start the isolated server first:
`source e2e/scripts/start_test_server.sh --bg`. The suite refuses to run if `QWENPAW_WORKING_DIR` is
unset or inside `$HOME` — E2E writes seed data and would corrupt a real `~/.qwenpaw`.

## Style gates (enforced by pre-commit, not negotiable)

**Never run `pre-commit` or `black` yourself** — not `pre-commit run`, not `pre-commit install`, not
`black --check`. The local venv's black is a different major version from the pinned hook, so running it
rewrites files the gate would have left alone. Match the surrounding style by hand and let the hook run
on the developer's side. `flake8`, `pytest`, `tsc -b`, `eslint` and `vitest` are fine to run.

black `--line-length=79`, flake8 `max-line-length=79`, mypy (many error codes disabled, `--follow-imports=skip`),
pylint (long disable list), `add-trailing-comma`, `fix-encoding-pragma` (every Python file starts with
`# -*- coding: utf-8 -*-`). Everything under `*/skills/*`, `scripts/pack/`, and `e2e/` is excluded.
Prettier runs on `.tsx?` but **excludes** `console/` — use `npm run format` there instead.

Conventional Commits for both commit messages and PR titles; scope must be lowercase.

## Architecture

### Working directory

All user state lives outside the repo in `WORKING_DIR`, resolved at *import time* in
`src/qwenpaw/constant.py`: `QWENPAW_WORKING_DIR` env → legacy `~/.copaw` → `~/.qwenpaw`. Secrets go to
the sibling `<WORKING_DIR>.secret`. Because it is frozen at import, tests cannot redirect it with a
fixture — `tests/conftest.py` sets the env var *before* the first `qwenpaw` import. Preserve that
ordering when touching conftest.

### Multi-agent: Workspace is the unit of isolation

`MultiAgentManager` owns N `Workspace` instances (`app/workspace/workspace.py`). Each Workspace is a
complete standalone runtime: its own ChannelManager, memory manager, DriverManager, CronManager, and
`WorkspacePlugins` (tool / hook / slash-command / prompt registries). Nothing is global.

HTTP routing mirrors this: every router in `app/routers/__init__.py` is mounted twice — flat, and again
under `/api/agents/{agentId}/...` via `create_agent_scoped_router()`. `AgentContextMiddleware` extracts
the agent id from the path or the `X-Agent-Id` header into a ContextVar. When adding a router, register
it in `routers/__init__.py` so it gets both mounts.

### Request lifecycle: Runtime + hooks + modes

`Workspace.stream_query` → `Runtime.run()` (`runtime/runtime.py`), an 8-phase orchestrator emitting SSE
envelopes. Phases (`runtime/phases.py`): PRE_DISPATCH, POST_DISPATCH, PRE_AGENT_BUILD, POST_AGENT_BUILD,
PRE_EXECUTE, POST_RESPONSE, ON_ERROR, FINALLY. Only two steps touch the agent — `AgentBuilder.build`
and `AgentExecutor` — everything else is a `LifecycleHook`.

An `AgentMode` (`modes/`: default, coding, mission, goal, custom_loop) bundles commands + tools + hooks
+ prompt contributors and registers all four through a single `setup(workspace)`. Mode-scoped hooks
inherit `ModeGatedHook`, which auto-skips when the mode is inactive — do not re-implement that gate.

These runtime hooks are orthogonal to `agentscope.middleware` middlewares, which wrap a single agent's
reply loop.

### Channels

`BaseChannel` (`app/channels/base.py`, ~2.3k lines) defines one contract: native payload → `content_parts`
(`TextContent` / `ImageContent` / `FileContent`) → `AgentRequest` → send path. Built-ins register in
`app/channels/registry.py`; third-party channels arrive via plugins calling `api.register_channel(...)`.
Contract tests in `tests/contract/channels/` are the compliance gate (`make check-contracts`).

### Harnesses (third-party coding agents)

`harnesses/` embeds Codex / Claude Code / Qoder as agent backends behind `HarnessAdapter`
(`harnesses/base.py`) with a static capability catalog in `harnesses/registry.py`. Each adapter maps a
vendor SDK's stream onto shared `HarnessEvent`s. Their SDKs are optional extras — a missing dependency
degrades to `MissingDependencyAdapter` rather than failing import.

### Other backend layers

- `providers/` — model providers; add and register in `provider_manager.py`. Must be natively
  OpenAI-`chat.completions`- or Anthropic-`messages`-compatible.
- `drivers/` — protocol-neutral external capability layer (MCP today) with credential storage and a
  per-call policy gate.
- `security/` (tool_guard, skill_scanner, secret_store), `governance/` (policy, audit, resource
  governor), `sandbox/` (per-OS backends: bubblewrap, macOS seatbelt, Windows AppContainer).
- `agents/skills/` — built-in skills, each a directory with `SKILL.md` front matter (`name`,
  `description`). Discovery is by presence; no registration. Descriptions must spell out trigger
  keywords (see CONTRIBUTING.md).

### Console

React 18 + Vite + Zustand + antd, alias `@` → `console/src`. `pages/` (Chat, Coding, Files, Control,
Inbox, Agent, Settings, AppCenter), `os/` (the desktop-shell UI with Dock / Launcher / window host),
`api/modules/*.ts` (one module per backend router, each with a colocated `.test.ts`), `stores/`.

Agent avatars (`icon` = `lucide:<name>:<color>`): `components/AgentAvatar/presets.ts` is the only list
of allowed icons and colors. After editing it, run `node scripts/export-agent-icons.mjs` in `console/` to
regenerate `src/qwenpaw/app/agent_avatar_icons.json`, which `GET /api/agents/{id}/avatar.svg` draws
from for native clients; `presets.test.ts` fails while the two disagree.

Vitest aliases the heavy `@agentscope-ai/chat` and `@agentscope-ai/design` packages to stubs in
`src/test/` to avoid OOM, and stubs `node_modules` CSS — that stub plugin must never run for real
builds, since removing monaco's stylesheet breaks Coding Mode's editor.

## Docs

User-facing docs are `website/public/docs/*.{en,zh}.md`. Update them when changing user-visible
behavior — adding a provider, channel, or skill each has a required docs entry per CONTRIBUTING.md.
