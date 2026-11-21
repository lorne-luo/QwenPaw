# Antigravity Harness

Optional, workspace-owned backend for the native Linux Antigravity CLI (`agy`)
using the public QwenPaw plugin SDK 1.1. It needs no Python vendor SDK; install
`agy` separately and sign in by running it interactively as the service user.
Registration declares metadata only; the first actual operation opens state and
resolves the CLI.

The backend keeps one persistent `agy --input-format stream-json` process per
conversation, resumes idle conversations by their exact ID with
`--conversation`, and reaps whole process groups on stop, reset and close. It
maps CLI steps to text and tool events, surfaces redacted stderr as a
diagnostics tool entry, reports conversation-scoped usage deltas only against a
persisted baseline, and lists `agy models` plus the CLI default model whose
efforts come from `agy -p /effort` (agy >= 1.1.11). Authentication is checked
read-only with `agy --print /usage` (agy >= 1.1.25); the tri-state result is
exposed as `account.authentication_status`. Login, logout, attachments, slash
commands, QwenPaw Skill/MCP projection and provider history are unsupported.

Session mappings live in this workspace's
`plugin-data/qwenpaw-harness-antigravity/antigravity_sessions.json`. A missing
target copies and validates `harnesses/antigravity_sessions.json` without
altering it. Existing valid target state wins; corrupt or unsupported state is
rejected without overwriting files. Legacy session keys are claimed once by a
trusted user/channel/session identity. Other users, channels and workspaces get
separate processes and mappings.

The host owns backend close. Close cancels idle timers and reaps every agy
process group while preserving mappings and the CLI's own conversations and
login. Bounded local filesystem and discovery work runs in workers and finishes
before cancellation releases its owner or mapping lock.

The native host and `full` extra do not include or enable this backend. Install
the verified plugin ZIP separately. Disable or uninstall preserves saved Agent
settings, materialized history, plugin data and the CLI login. A saved Agent
with an unavailable backend remains manageable and returns an explicit
unavailable error when run.

For an upgrade from the old builtin backend, stop the old host and prepare the
verified plugin package before starting the new host. Run the shared migration
first with `--dry-run`, then `--apply`:

```sh
QWENPAW_WORKING_DIR="$HOME/.qwenpaw" qwenpaw plugin migrate-legacy-harnesses \
  --plugin-dir /path/to/verified-local-packages --dry-run
QWENPAW_WORKING_DIR="$HOME/.qwenpaw" qwenpaw plugin migrate-legacy-harnesses \
  --plugin-dir /path/to/verified-local-packages --apply
```

See the [Qoder README](../qoder/README.md) for the shared migration semantics.

## Frontend build

The plugin owns its locale assets and wraps the private shared settings panel and
connection hook in `../_shared/ui`. Keep the sibling `../_shared` source
directory when building. Install normal build dependencies, then the actual
public SDK npm directory (for a checkout, `src/qwenpaw/plugins/frontend`):

```sh
cd frontend
npm install
npm install --no-save --package-lock=false /path/to/qwenpaw-plugin-sdk
npm run build
```

`frontend/dist/index.js` is the self-contained manifest entry; source and build
files are excluded from the runtime archive. Build before packing:

```sh
python scripts/pack/generate_plugin_metadata.py --plugins-root plugins \
  --dist dist/plugins --metadata-out dist/plugins/index.json \
  --cdn-prefix /files/plugins --only qwenpaw-harness-antigravity
```

## Core tests

On Linux, from the repository root with the host SDK and
`requirements-test.txt` installed, run
`python -m pytest plugins/harness/antigravity/tests/test_antigravity_harness.py -q`.
The suite uses inert local `agy` scripts and recorded NDJSON fixtures and
installs the built ZIP through the host; real accounts and CLI versions remain
release acceptance steps.

## Shared IO helper maintenance

Edit only `plugins/harness/_shared/io_helpers.py`, then run from the repository root:

```sh
python scripts/pack/sync_harness_helpers.py --write
python scripts/pack/sync_harness_helpers.py --check
```

`--plugin antigravity` explicitly selects this plugin.
