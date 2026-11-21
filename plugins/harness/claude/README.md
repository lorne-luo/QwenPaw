# Claude Code Harness

Optional, workspace-owned Claude Code backend using the public QwenPaw plugin SDK
1.1 and `claude-agent-sdk==0.2.186`. Install this plugin separately from the
native QwenPaw runtime. Registration declares metadata only; the first actual
operation opens state, resolves the CLI and creates SDK clients as needed.

The backend preserves Claude Code streaming, attachments, models and reasoning
effort, permission callbacks, the managed MCP tool policy gate, and effective
QwenPaw Skill/MCP projection. The model picker comes from the CLI handshake,
which already reflects gateway routing and per-model effort support; a static
alias catalog (`default`, `fable`, `opus`, `sonnet`, `haiku`) is the fallback
when the CLI is missing or unreachable.

## Host requirement: command passthrough

The spec sets `HarnessCapabilities.passthrough_commands`, a field this fork adds
to the core SDK. Claude Code parses slash commands itself, so the host hands an
unlisted command such as `/init` to `run_turn` verbatim instead of rejecting it,
and a long command keeps streaming. The declared `commands` (init, compact,
context, usage, doctor, mcp) only populate the Console's slash menu. `/new` and
`/clear` stay with the host. A host without that field rejects this plugin's
registration.

## Environment and sign-in

Each Agent's `backend_settings.env` reaches the CLI, so one Agent can route
through a gateway (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
`ANTHROPIC_DEFAULT_OPUS_MODEL`, ...) while another inherits the host's own
Claude Code authentication. The settings panel edits it as ordered name/value
rows; a hand-edited mapping is accepted too. Values are stored in plain text.
Status probing and the fallback catalog read the same merged environment the
CLI sees: `~/.claude/settings.json` `env`, then the QwenPaw process, then the
Agent's own variables. The env travels with every call rather than being an
instance setting, so editing it reconnects that Agent's sessions without
replacing a busy backend.

Sign-in is declined with guidance: the CLI prints an OAuth URL and then reads
the code from its own stdin, so run `claude auth login` on the host or add a
token to the Agent's environment. Status and logout work non-interactively.

## Sessions

Session mappings live in this workspace's
`plugin-data/qwenpaw-harness-claude/claude_sessions.json`. A missing target
copies and validates the in-core backend's `harnesses/claude_sessions.json`
without altering it; corrupt state is rejected without overwriting files.
Legacy session-ID keys are claimed once by a trusted user/channel/session
identity, so other users and channels get separate clients and mappings.

A fresh conversation's ID is recorded only after the CLI answers, because Claude
Code writes the transcript that ID names only once a turn produces output. If a
saved conversation no longer exists on the host, the next turn starts a fresh
one and says so. Reset drops the mapping and disconnects the client; Claude
Code's transcript stays on the host.

One reader drains each SDK client for its lifetime. The SDK buffers at most 100
unread messages and routes permission and hook requests through the same
stream, so an unread client would stall a background agent. Each QwenPaw turn
receives only the messages of the CLI turn its prompt started
(`--replay-user-messages` shows whether the CLI folded a prompt into a turn
already under way). A permission request that arrives while no QwenPaw call is
live is denied, because no approver can be reached. A client whose stream has
ended is never reused.

## Frontend build

The plugin owns its locale assets and wraps the shared settings panel in
`../_shared/ui`, adding the environment editor and hiding Connect. Build it as
described in the [Qoder frontend notes](../qoder/README.md#frontend-build), or
for this plugin alone:

```sh
cd frontend
npm install --include=dev --no-save --package-lock=false \
  ../../../../src/qwenpaw/plugins/frontend
npm run build
```

## Tests

With the host SDK and `requirements-test.txt` installed, run from the
repository root:

```sh
python -m pytest plugins/harness/claude/tests -q
```

Tests use fake SDK clients and inert process fixtures; real accounts and CLI
operation are separate checks. Edit only `plugins/harness/_shared/io_helpers.py`
and regenerate this plugin's copy with
`python scripts/pack/sync_harness_helpers.py --write --plugin claude`.
