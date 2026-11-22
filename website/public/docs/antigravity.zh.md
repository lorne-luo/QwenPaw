# Antigravity Agent 后端

QwenPaw 可以通过 Linux 主机上的 Antigravity CLI 进行文字对话。在 Agent 设置中选择 **第三方 Agent → Antigravity**。后端驱动 CLI 的 Agent Client Protocol 服务 `agy acp`。

## 安装插件

Antigravity 后端是可选插件 `qwenpaw-harness-antigravity`（源码位于 `plugins/harness/antigravity`）。QwenPaw 原生运行时和 `full` extra 都不包含也不启用它，它也不需要 Python 厂商 SDK。先构建前端并打包，再安装校验过的插件 ZIP：

```sh
bash scripts/pack/build_harness_frontends.sh
python scripts/pack/generate_plugin_metadata.py --plugins-root plugins \
  --dist dist/plugins --metadata-out dist/plugins/index.json \
  --cdn-prefix /files/plugins --only qwenpaw-harness-antigravity
qwenpaw plugin install \
  dist/plugins/harness/qwenpaw-harness-antigravity/qwenpaw-harness-antigravity-1.0.0.zip
```

从旧版内置 Antigravity 后端升级时，先停止旧服务，再对存放已准备 ZIP 的目录依次以 `--dry-run`、`--apply` 运行共享迁移：

```sh
QWENPAW_WORKING_DIR="$HOME/.qwenpaw" qwenpaw plugin migrate-legacy-harnesses \
  --plugin-dir /path/to/verified-local-packages --dry-run
QWENPAW_WORKING_DIR="$HOME/.qwenpaw" qwenpaw plugin migrate-legacy-harnesses \
  --plugin-dir /path/to/verified-local-packages --apply
```

迁移只规划后端为 `antigravity` 的 Agent，重复 apply 不会产生额外变化。每个 Agent 工作区首次使用时，插件把旧的 `harnesses/antigravity_sessions.json` 复制到 `plugin-data/qwenpaw-harness-antigravity/antigravity_sessions.json`，不修改原文件，因此已有会话可以继续恢复。插件目录中已有的有效状态优先；损坏的状态会报错，且不会被覆盖。禁用或卸载插件会保留 Agent 设置、历史、插件数据和 CLI 自身的登录；在重新安装插件前，已保存的 Agent 会显示后端不可用。

## 安装和登录

根据 [Google 安装文档](https://www.antigravity.google/docs/cli/install/) 安装 `agy`。以运行 QwenPaw 服务的同一系统用户交互式执行 `agy`，完成登录。**连接** 按钮无法代为登录，只会提示这一步。

可执行文件优先使用显式配置（支持绝对路径和 `~/`）；留空时依次搜索服务进程的 PATH 和服务用户的 `~/.local/bin/agy`。显式配置错误时不会改用其他安装。systemd 服务通常不会加载终端的 shell 配置，因此 `which agy` 成功不代表服务 PATH 也能找到它。

QwenPaw 启动一个短暂的 `agy acp` 并列出会话来验证认证，不发送模型提示，也不新建会话。列出成功即为已登录；服务返回 “Authentication required” 即为未登录；超时和其他失败保持未验证，并保留尝试聊天的能力。`agy acp` 无法启动或不支持恢复会话的 CLI 会显示为不可用。设置页对已登录以外的状态都显示“认证状态未验证”，下方注明原因；状态接口通过 `account.authentication_status`（`authenticated`、`unauthenticated` 或 `unknown`）给出确切状态。

首次认证查询最多等待 15 秒（进程回收可能额外耗时）。已登录结果最多缓存 60 秒，未登录或未知结果最多缓存 5 秒；手动登录后稍等再点击 **重新检测**。QwenPaw 不删除 CLI 凭证，也不替用户完成浏览器登录。

## 会话和设置

在聊天控制区选择模型；模型来自 ACP 会话报告的模型列表。列表中的模型已在名称中包含推理档位（如 `gemini-3.8-flash-low`），因此只有 **Antigravity default**（CLI 默认模型）可以选择推理强度，可选档位以会话的 `thought_level` 选项为准。列表读取自一个可复用、除此之外为空的目录会话，并缓存 10 分钟。获取失败时仍可使用 CLI 默认模型，失败原因记录在该条目的描述中。设置在下一轮生效。

每个 QwenPaw 会话按用户、渠道和会话分别对应独立的 CLI conversation，全部由同一个 `agy acp` 进程服务。会话空闲 `idle_timeout_seconds` 后关闭，没有会话时进程退出，下次使用精确 ID 恢复。ACP 会话 ID 就是 CLI conversation ID，因此旧版插件创建的会话可直接恢复。QwenPaw 本地没有该会话的聊天记录时，会从 CLI 导入历史。QwenPaw 重启后保留映射与本地聊天历史；恢复失败不会静默新建会话。使用 `/new` 或 `/clear` 显式重新开始。工作目录改变后需要新建会话。

可通过 `backend_settings` 配置：

| 设置 | 默认值 | 允许值 |
| --- | --- | --- |
| `binary` | PATH，再查用户安装目录 | 可执行文件路径 |
| `permission_mode` | `configured` | `configured`、`accept-edits`、`plan`、`full-access` |
| `print_timeout_seconds` | 3600 | 1–3600 的整数 |
| `idle_timeout_seconds` | 600 | 30–86400 的整数 |

模型保存为 `model`（`default` 或留空表示 CLI 默认模型），聊天推理强度保存为 `reasoning_effort`（后端也接受 `effort`），必须是 CLI 为默认模型提供的档位（如 `low`、`medium`、`high`、`xhigh`）；`model` 指定列表中的模型时忽略该值。

## 权限和停止

**操作前询问**（`configured`）遵循 CLI 自己的权限规则（其设置中的 `toolPermission`）。CLI 请求权限时，QwenPaw 弹出审批；批准只放行这一次调用，拒绝或超时（5 分钟）则拒绝。两轮之间到达的请求一律拒绝。**接受编辑**和**计划**把会话切换到 CLI 的 `accept-edits` 和 `plan` 模式。**完全访问**自动批准所有权限请求，QwenPaw 不会自动升级到此模式。

停止会发送 ACP 取消并最多等待 10 秒让本轮结束，会话保持加载；未能及时停止的会话会被关闭，下一条消息时恢复。关闭后端会回收整个进程组。停止不会撤销文件修改或外部操作，也不保证停止厂商独立脱离进程组的后台任务；被中断的提示不会自动重发。

## 限制与排查

仅支持文字，不支持图片/文件附件、交互式提问、QwenPaw MCP/Skills 投射、任意 CLI 斜杠命令。CLI 仍可能使用自己的工具与配置；QwenPaw 工具白名单不会投射到此后端。QwenPaw 的 `/new` 和 `/clear` 仍可使用。

- 后端不可用：安装或启用 `qwenpaw-harness-antigravity` 插件。
- 找不到 CLI：为服务用户安装，或填写正确可执行文件路径。
- 登录失败：以服务用户交互式运行 `agy` 重新登录。
- 会话状态错误：恢复插件数据目录中的 `antigravity_sessions.json`，或修复其文件权限。
- 恢复失败：保留会话重试，或显式新建会话。
- 超时：检查工具结果，必要时调整 `print_timeout_seconds`；不会自动重放任务。
- 协议错误：检查 CLI 版本。ACP 接入针对 1.5.14（ACP 协议版本 1）进行了验证。
