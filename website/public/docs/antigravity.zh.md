# Antigravity Agent 后端

QwenPaw 可以通过 Linux 主机上的 Antigravity CLI 进行文字对话。在 Agent 设置中选择 **第三方 Agent → Antigravity**。适配器使用 CLI 的 headless 流式接口，不使用 ACP。

## 安装和登录

根据 [Google 安装文档](https://www.antigravity.google/docs/cli/install/) 安装 `agy`。以运行 QwenPaw 服务的同一系统用户交互式执行 `agy`，完成登录。不需要额外 Python SDK。

可执行文件优先使用显式配置（支持绝对路径和 `~/`）；留空时依次搜索服务进程的 PATH 和服务用户的 `~/.local/bin/agy`。显式配置错误时不会改用其他安装。systemd 服务通常不会加载终端的 shell 配置，因此 `which agy` 成功不代表服务 PATH 也能找到它。

对于支持的 CLI 版本，QwenPaw 使用 `agy --print /usage` 查询账户配额来验证认证，不发送模型提示。收到有效配额报告显示已登录；CLI 明确要求认证时显示未登录；网络失败、超时、未知输出或不支持的版本显示“认证状态未验证”，并保留尝试聊天的能力。检测目前适用于 `1.1.25` 及之后的版本。配额不可用不代表凭据失效。

首次认证查询最多等待 15 秒（进程回收可能额外耗时）。已登录结果最多缓存 60 秒，未登录或未知结果最多缓存 5 秒；手动登录后稍等再刷新设置。QwenPaw 不删除 CLI 凭证，也不替用户完成浏览器登录。

## 会话和设置

在聊天控制区选择模型；模型来自 `agy models`。列表中的模型已在名称中包含推理档位（如 `gemini-3.8-flash-low`），因此只有 CLI 默认模型可以选择推理强度，可选档位以 `agy` 报告的为准。获取失败时仍可使用 CLI 默认模型，并显示失败原因。设置在下一轮生效。

每个 QwenPaw 会话有独立的 CLI conversation。空闲进程自动释放，下次使用精确 ID 恢复。QwenPaw 重启后保留映射与本地聊天历史；恢复失败不会静默新建会话。使用 `/new` 或 `/clear` 显式重新开始。工作目录改变后需要新建会话。

可通过 `backend_settings` 配置：

| 设置 | 默认值 | 允许值 |
| --- | --- | --- |
| `binary` | PATH，再查用户安装目录 | 可执行文件路径 |
| `permission_mode` | `configured` | `configured`、`full-access` |
| `print_timeout_seconds` | 300 | 1–3600 的整数 |
| `idle_timeout_seconds` | 600 | 30–86400 的整数 |

模型保存为 `model`，聊天推理强度保存为 `reasoning_effort`（适配器也接受 `effort`），允许 `low`、`medium`、`high`、`max`；`model` 指定列表中的模型时忽略该值。

## 权限和停止

**CLI 当前配置**遵循 CLI 已有权限规则，不代表只读，也不能弹出交互审批。工具被拒绝后整轮仍可能成功，请查看工具结果和诊断。**完全访问**会显式跳过 CLI 工具权限检查，QwenPaw 不会自动升级到此模式。

停止会中断并回收当前 CLI 进程组，不会撤销文件修改或外部操作，也不保证停止厂商独立脱离进程组的后台任务。下一条消息尝试恢复原会话；被中断的提示不会自动重发。

## 限制与排查

仅支持文字，不支持图片/文件附件、交互式提问或审批回传、厂商历史导入、QwenPaw MCP/Skills 投射、任意 CLI 斜杠命令。CLI 仍可能使用自己的工具与配置；QwenPaw 工具白名单不会投射到此后端。QwenPaw 的 `/new` 和 `/clear` 仍可使用。

- 找不到 CLI：为服务用户安装，或填写正确可执行文件路径。
- 登录失败：以服务用户交互式运行 `agy` 重新登录。
- 恢复失败：保留会话重试，或显式新建会话。
- 超时：检查工具诊断，必要时调整 `print_timeout_seconds`；不会自动重放任务。
- 协议错误：检查 CLI 版本。最初接入针对 1.1.15 进行了协议探测；仅存在参数不代表所有版本都兼容。
