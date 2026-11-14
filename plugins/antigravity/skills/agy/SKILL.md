---
name: agy
description: "Use Codex AGY Supervisor only when the user invokes Antigravity or explicitly requests using Antigravity, AGY, or codex_agy for the task. Explicit requests include 'Dùng Antigravity để sửa lỗi này' and 'Dùng AGY kiểm tra task'. Ordinary coding requests and questions about Antigravity do not authorize execution."
---

# Antigravity

Proceed only when the current request explicitly selects this plugin or asks to use Antigravity, AGY, or codex_agy. Continue an existing AGY execution when the user asks to resume that execution.

Read [runtime.json](runtime.json) next to this skill for the installed Python, Supervisor config and launcher script. Invoke the reported paths directly in PowerShell:

```powershell
& $AgyPython -m codex_agy.cli --config $AgyConfig health
& $AgyPython -m codex_agy.cli --config $AgyConfig schema submit_plan
& $AgyPython -m codex_agy.cli --config $AgyConfig call $Tool --json-file $PayloadPath
```

A response has `ok` and either `data` or `error`. Shell calls need permission to reach the local HTTP endpoint at `127.0.0.1`. If the Codex sandbox blocks the connection, use its normal approval flow for the scoped command. If the Supervisor is stopped and the user requested execution, use the launcher from runtime.json. A status-only request uses the corresponding read operation. Write JSON arguments to UTF-8 files, or omit `--json-file` for a tool that accepts an empty object. Use `schema <tool>` when the argument shape is needed.

1. Inspect the target Git repo and its working changes. Plan against committed HEAD, with checkable criteria, dependency order and concrete verification argv. Use `submit_plan` and `execute_plan`.
2. Keep the execution and task IDs. Wait with `wait_for_completion`, using short bounded waits. Resolve only one-time permissions covered by the user's request. On an interruption, inspect the retained worktree before retrying. For a resumed review, use the provided task ID or `list_tasks`.
3. For `REVIEW_REQUIRED`, read the current revision and diff from `get_review_context`, inspect the source and run independent checks. Send actionable findings through `request_fix` or accept the verified revision with `accept_milestone`. Continue when `next_task_id` is returned. Report the final revisions and verification results. Merge, push, and deploy require separate authorization.
