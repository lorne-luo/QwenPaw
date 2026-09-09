# Antigravity agent backend

QwenPaw can run text conversations through the local Antigravity CLI on Linux. Select **Third-party agent → Antigravity** in agent settings. The adapter uses the CLI's headless stream, not ACP.

## Setup

Install `agy` using [Google's installation instructions](https://www.antigravity.google/docs/cli/install/). Run `agy` interactively as the same operating-system user that runs the QwenPaw service and finish signing in. No Python SDK extra is required.

An explicit executable takes priority (absolute paths and `~/` are supported). With the field empty, discovery searches the service process's PATH, then the service user's `~/.local/bin/agy`. An invalid explicit path fails without falling back to another installation. Systemd services usually do not load interactive shell configuration, so a successful `which agy` in a terminal does not establish that the service PATH includes it.

For supported CLI versions, QwenPaw verifies authentication using the account quota report from `agy --print /usage`, without sending a model prompt. A valid report means authenticated; an explicit authentication requirement means not signed in. Network failures, timeouts, unrecognized output, and unsupported versions remain “Authentication not verified” and do not prevent trying a chat. Detection currently covers versions from `1.1.25` onward. Unavailable quota information does not establish invalid credentials.

The initial authentication probe waits up to 15 seconds, plus process cleanup. Authenticated results are cached for at most 60 seconds; unauthenticated or unknown results for at most 5 seconds. After signing in manually, wait briefly and refresh settings. QwenPaw does not remove CLI credentials or complete a browser login for you.

## Conversations and settings

Choose a model in the chat controls. Available models come from `agy models`; each listed model already names its reasoning level (for example `gemini-3.8-flash-low`), so reasoning effort is selectable only for the CLI default model, limited to the levels `agy` reports for it. When discovery fails, the CLI default remains usable and a diagnostic explains the failure. Settings apply on the next turn.

Each QwenPaw session has a separate CLI conversation. Idle processes are released and resumed by their exact conversation ID when needed. Restarting QwenPaw retains this mapping and the QwenPaw transcript. A failed resume is reported without silently starting a new conversation. Use `/new` or `/clear` to explicitly start over. Changing workspace directory requires a new conversation.

Additional `backend_settings` values:

| Setting | Default | Valid values |
| --- | --- | --- |
| `binary` | PATH, then user installation directory | Executable path |
| `permission_mode` | `configured` | `configured`, `full-access` |
| `print_timeout_seconds` | 300 | Integer 1–3600 |
| `idle_timeout_seconds` | 600 | Integer 30–86400 |

Model selection is stored as `model`; chat effort is stored as `reasoning_effort` (the adapter also accepts `effort`), accepts `low`, `medium`, `high`, or `max`, and is ignored when `model` names a listed model.

## Permissions and stopping

**CLI configuration** uses the CLI's existing permission rules. It does not mean read-only and cannot display an interactive approval dialog. A tool may be denied while the rest of the answer succeeds; inspect the tool result and diagnostics. **Full access** explicitly skips CLI tool permission checks. QwenPaw never automatically switches to this mode.

Stop interrupts the current CLI process group and reaps it. It does not undo file edits, retract external actions, or guarantee that independently detached vendor tasks stop. The next message attempts to resume the conversation; interrupted prompts are never automatically replayed.

## Limitations and troubleshooting

Text only: image/file attachments, interactive questions/approvals, provider transcript import, QwenPaw MCP/Skills projection, and arbitrary CLI slash commands are not supported. The CLI may use its own tools and configuration; QwenPaw tool allowlists are not projected into this backend. Native `/new` and `/clear` remain available.

- CLI missing: install it for the service user or set the executable path.
- Login error: sign in through interactive `agy` as the service user.
- Restore error: preserve the existing conversation, retry, or explicitly start a new one.
- Timeout: inspect tool diagnostics, then adjust `print_timeout_seconds` if appropriate; no automatic replay occurs.
- Protocol error: check the CLI version. The initial integration was probed against 1.1.15; flags alone do not prove compatibility with every version.
