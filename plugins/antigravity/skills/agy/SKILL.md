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
& $AgyPython -m codex_agy.cli --config $AgyConfig wait $TaskId --seconds 1800
```

A response has `ok` and either `data` or `error`. Shell calls need permission to reach the local HTTP endpoint at `127.0.0.1`. If the Codex sandbox blocks the connection, use its normal approval flow for the scoped command. If the Supervisor is stopped and the user requested execution, use the launcher from runtime.json. A status-only request uses the corresponding read operation. Write JSON arguments to UTF-8 files, or omit `--json-file` for a tool that accepts an empty object. Use `schema <tool>` when the argument shape is needed.

1. Inspect the target Git repo and its working changes. Write one plan against committed HEAD before implementation, with checkable criteria, milestones, dependency order and verification argv. Use `submit_plan` once and keep the same plan version across milestones and fixes. Use `execute_plan` to run each milestone in order. Revise the plan only if the user changes the goal or a blocker invalidates an assumption. AGY owns implementation and fixes. Keep Codex's work to planning, review and focused independent checks. Edit implementation files yourself only if the user explicitly asks you to take over.
2. Keep the execution and task IDs. Prefer the plugin's `agy_wait` MCP tool `wait_for_review`, passing `task_id` and `timeout_seconds: 1800`. When the host exposes tools through a code execution wrapper, set that wrapper's `yield_time_ms` to 1800000 or its supported maximum. With the `functions.exec` wrapper, put `// @exec: {"yield_time_ms": 1800000}` on the first line. The wrapper's default 30-second yield can trigger extra model requests even while the same MCP call is pending. If a cell handle still returns, wait on that cell with the supported maximum interval. If the MCP tool is unavailable, use the CLI `wait` command. Use `get_task_status` for a one-off status request or to diagnose an error. When the bounded wait expires, continue waiting if the user requested completion. A disconnected chat can resume by task ID. Resolve only one-time permissions covered by the user's request. On a Supervisor interruption, inspect the retained worktree before retrying.
3. For `REVIEW_REQUIRED`, use the cached review summary to locate the Git diff. Inspect the diff before opening any whole file. Use the recorded verification for that revision and run targeted independent checks for uncovered criteria or regression risk. A failed, skipped, or unavailable check needs attention when it covers a required criterion. Send material findings in one `request_fix`; leave optional style suggestions out of the fix loop. On the next revision, start with `fix_diff` and review the change and behavior it can affect. Preserve any important security, correctness, and compatibility checks. Accept once the criteria are met, then continue when `next_task_id` is returned. Report concise outcomes at completion or when blocked. Merge, push, and deploy require separate authorization.

The Supervisor handles permissions covered by its local `permission_mode` before waking the waiter. Keep the same long wait while those requests are handled. If the wait returns `WAITING_PERMISSION`, inspect that specific request. Report it as a blocker if it needs user authorization. Leave verification commands declared in the plan to the Supervisor.
