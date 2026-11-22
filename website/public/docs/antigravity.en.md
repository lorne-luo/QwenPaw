# Antigravity agent backend

QwenPaw can run text conversations through the local Antigravity CLI on Linux. Select **Third-party agent → Antigravity** in agent settings. The backend drives `agy acp`, the CLI's Agent Client Protocol server.

## Plugin installation

The Antigravity backend is the optional `qwenpaw-harness-antigravity` plugin (source: `plugins/harness/antigravity`). The native QwenPaw runtime and the `full` extra do not include or enable it, and it needs no Python vendor SDK. Build its frontend and package it, then install the verified plugin ZIP:

```sh
bash scripts/pack/build_harness_frontends.sh
python scripts/pack/generate_plugin_metadata.py --plugins-root plugins \
  --dist dist/plugins --metadata-out dist/plugins/index.json \
  --cdn-prefix /files/plugins --only qwenpaw-harness-antigravity
qwenpaw plugin install \
  dist/plugins/harness/qwenpaw-harness-antigravity/qwenpaw-harness-antigravity-1.0.0.zip
```

When upgrading from the old built-in Antigravity backend, stop the old host first and run the shared migration with `--dry-run`, then `--apply`, against the directory containing the prepared ZIP:

```sh
QWENPAW_WORKING_DIR="$HOME/.qwenpaw" qwenpaw plugin migrate-legacy-harnesses \
  --plugin-dir /path/to/verified-local-packages --dry-run
QWENPAW_WORKING_DIR="$HOME/.qwenpaw" qwenpaw plugin migrate-legacy-harnesses \
  --plugin-dir /path/to/verified-local-packages --apply
```

Only Agents whose backend is `antigravity` are planned, and repeated apply is idempotent. On first use in each Agent workspace, the plugin copies the old `harnesses/antigravity_sessions.json` into `plugin-data/qwenpaw-harness-antigravity/antigravity_sessions.json` without changing the original, so existing conversations keep resuming. Existing valid plugin state wins; corrupt state is reported and never overwritten. Disabling or uninstalling the plugin keeps saved Agent settings, history, plugin data and the CLI's own login; a saved Agent then reports the backend as unavailable until the plugin is installed again.

## Setup

Install `agy` using [Google's installation instructions](https://www.antigravity.google/docs/cli/install/). Run `agy` interactively as the same operating-system user that runs the QwenPaw service and finish signing in. The **Connect** button cannot sign in for you; it explains this step.

An explicit executable takes priority (absolute paths and `~/` are supported). With the field empty, discovery searches the service process's PATH, then the service user's `~/.local/bin/agy`. An invalid explicit path fails without falling back to another installation. Systemd services usually do not load interactive shell configuration, so a successful `which agy` in a terminal does not establish that the service PATH includes it.

QwenPaw verifies authentication by starting a short-lived `agy acp` and listing sessions, without sending a model prompt or creating a conversation. A successful listing means authenticated; the server's “Authentication required” answer means not signed in. Timeouts and other failures remain unverified and do not prevent trying a chat. A CLI whose `agy acp` cannot start or cannot resume sessions is reported as unavailable. Settings show any state other than authenticated as “Authentication not verified” with the reason below it; the status API reports the exact state as `account.authentication_status` (`authenticated`, `unauthenticated` or `unknown`).

The initial authentication probe waits up to 15 seconds, plus process cleanup. Authenticated results are cached for at most 60 seconds; unauthenticated or unknown results for at most 5 seconds. After signing in manually, wait briefly and select **Detect**. QwenPaw does not remove CLI credentials or complete a browser login for you.

## Conversations and settings

Choose a model in the chat controls. Available models come from the models an ACP session reports; each listed model already names its reasoning level (for example `gemini-3.8-flash-low`), so reasoning effort is selectable only for **Antigravity default** (the CLI default model), limited to the session's `thought_level` choices. The list is read from one reusable, otherwise empty catalog conversation and cached for 10 minutes. When discovery fails, the CLI default remains usable and the failure reason is recorded as that entry's description. Settings apply on the next turn.

Each QwenPaw session has a separate CLI conversation, kept per user, channel and session; one `agy acp` process serves all of them. Idle sessions are closed after `idle_timeout_seconds`, the process stops once none remains, and conversations are resumed by their exact ID when needed. The ACP session ID is the CLI conversation ID, so conversations started by earlier plugin versions resume unchanged. When QwenPaw has no local transcript for a session, it imports the conversation from the CLI. Restarting QwenPaw retains this mapping and the QwenPaw transcript. A failed resume is reported without silently starting a new conversation. Use `/new` or `/clear` to explicitly start over. Changing workspace directory requires a new conversation.

Additional `backend_settings` values:

| Setting | Default | Valid values |
| --- | --- | --- |
| `binary` | PATH, then user installation directory | Executable path |
| `permission_mode` | `configured` | `configured`, `accept-edits`, `plan`, `full-access` |
| `print_timeout_seconds` | 3600 | Integer 1–3600 |
| `idle_timeout_seconds` | 600 | Integer 30–86400 |

Model selection is stored as `model` (`default` or empty means the CLI default); chat effort is stored as `reasoning_effort` (the backend also accepts `effort`), must be a level the CLI offers for its default model (for example `low`, `medium`, `high` or `xhigh`), and is ignored when `model` names a listed model.

## Permissions and stopping

**Ask before actions** (`configured`) follows the CLI's own permission rules (`toolPermission` in its settings). When the CLI asks for permission, QwenPaw shows an approval request; approving allows that call once, rejecting or letting it time out (5 minutes) denies it. Requests that arrive between turns are denied. **Accept edits** and **Plan** switch the session to the CLI's `accept-edits` and `plan` modes. **Full access** approves every permission request automatically. QwenPaw never automatically switches to this mode.

Stop sends an ACP cancel and waits up to 10 seconds for the turn to end, keeping the conversation loaded; a turn that does not stop in time has its session closed and resumed on the next message. Closing the backend reaps the whole process group. Stop does not undo file edits, retract external actions, or guarantee that independently detached vendor tasks stop. Interrupted prompts are never automatically replayed.

## Limitations and troubleshooting

Text only: image/file attachments, interactive questions, QwenPaw MCP/Skills projection, and arbitrary CLI slash commands are not supported. The CLI may use its own tools and configuration; QwenPaw tool allowlists are not projected into this backend. Native `/new` and `/clear` remain available.

- Backend unavailable: install or enable the `qwenpaw-harness-antigravity` plugin.
- CLI missing: install it for the service user or set the executable path.
- Login error: sign in through interactive `agy` as the service user.
- Session state error: restore `antigravity_sessions.json` in the plugin data directory, or fix its file permissions.
- Restore error: preserve the existing conversation, retry, or explicitly start a new one.
- Timeout: inspect the tool results, then adjust `print_timeout_seconds` if appropriate; no automatic replay occurs.
- Protocol error: check the CLI version. The ACP integration was verified against 1.5.14 (ACP protocol version 1).
