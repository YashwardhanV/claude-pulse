# Claude Code hooks — what was verified (2026-10-15, Claude Code 2.1.300; updated 2026-11-17)

Sources: <https://code.claude.com/docs/en/hooks>, <https://code.claude.com/docs/en/statusline>,
plus real transcripts under `~/.claude/projects/` on this machine.

## Hook events

The docs list 33 events. The ones Claude Pulse uses:

| Event | Used for | Matcher support |
|---|---|---|
| `SessionStart` | create card, auto-start server (`source`: startup/resume/clear/compact/fork) | yes |
| `UserPromptSubmit` | start of a task (we never read `prompt`) | **no** |
| `PreToolUse` | `AskUserQuestion` / `ExitPlanMode` only → needs you | yes (tool name) |
| `PostToolUse` | tool spike, `working`, error detection in `tool_response` (async) | yes |
| `PostToolUseFailure` | **dedicated tool-failure event** → red spike (`error`, `is_interrupt`) (async) | yes |
| `PermissionRequest` | permission dialog shown → `blocked` | yes |
| `Notification` | `blocked` (`notification_type`: `permission_prompt`, `idle_prompt`, `elicitation_dialog`, `agent_needs_input`, …) | yes |
| `Stop` | `done` | **no** |
| `SubagentStop` | flutter pulse | yes (agent type) |
| `PreCompact` | deep-breath animation (`trigger`: manual/auto) (async) | yes |
| `SessionEnd` | `ended` (`reason`: clear/resume/logout/prompt_input_exit/other). Shares a 1.5 s budget. | yes |

Other events that exist but are not used: `Setup`, `UserPromptExpansion`, `StopFailure`,
`PermissionDenied`, `PostToolBatch`, `SubagentStart`, `TaskCreated`, `TaskCompleted`,
`TeammateIdle`, `InstructionsLoaded`, `ConfigChange`, `CwdChanged`, `DirectoryAdded`,
`FileChanged`, `WorktreeCreate`, `WorktreeRemove`, `PostCompact`, `PreModelSwitch`,
`PostModelSwitch`, `MessageDisplay`, `Elicitation`, `ElicitationResult`.

## stdin JSON

Common to every event: `session_id`, `transcript_path`, `cwd`, `hook_event_name`,
`permission_mode`, `prompt_id`, `scratchpad_dir`; `agent_id`/`agent_type` inside subagents.

* Tool events add `tool_name`, `tool_input`, `tool_use_id`; `PostToolUse` adds `tool_response`
  (shape depends on the tool) and optional `duration_ms`.
* `PostToolUseFailure` adds `error` (string; for Bash/PowerShell the first line is
  `Exit code N`) and optional `is_interrupt`.
* `Notification` adds `message`, optional `title`, `notification_type`.
* `Stop`/`SubagentStop` add `stop_hook_active`, `last_assistant_message`, …
* `PreCompact` adds `trigger`, `custom_instructions`.
* `SessionEnd` adds `reason`.

Claude Pulse only reads: `session_id`, `transcript_path`, `cwd`, `hook_event_name`,
`tool_name`, a summary of `tool_input` (file basename / first 40 chars of a shell command),
error flags from `tool_response`, `is_interrupt`, `notification_type`, `source`, `reason`,
`trigger`, `agent_type`. Prompts, messages and file contents are dropped on arrival.

## settings.json format

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "…", "timeout": 5 } ] }
    ]
  }
}
```

* Matcher: `"*"`, `""` or omitted = all; `A|B` = list; anything else = JS regex.
  On events without matcher support (`UserPromptSubmit`, `Stop`) a matcher is ignored, so we omit it.
* Handler `timeout` is in seconds (default 600 for command hooks). We set 5.
* Handlers in the same group run in parallel.
* Hooks are read at session start → restart Claude Code after installing; check with `/hooks`.

## Shell on Windows

Command hooks run through **Git Bash** when it is installed, otherwise **PowerShell**
(override per handler with `"shell": "bash" | "powershell"`).
Our hook command is written so it parses identically in both shells:

```
curl.exe -s -m 1 -o NUL -H "Content-Type: application/json" --data-binary "@-" http://127.0.0.1:7777/event ; exit 0 # claude-pulse
```

* `curl.exe` (not `curl`) — in Windows PowerShell 5.1 `curl` is an alias of `Invoke-WebRequest`.
* `"@-"` is quoted because a bare `@` is splatting syntax in PowerShell.
* `-o NUL` discards the response body; stdout stays empty, so nothing is parsed as hook JSON.
* `; exit 0` works in both shells; `|| true` would be a parse error in PowerShell 5.1.
* The trailing `# claude-pulse` comment is the marker the installer uses to find its own entries.

## Exit codes

* `0` — success. Stdout is parsed as JSON if it is one JSON object (so we print nothing).
* `2` — **blocking**: blocks the tool in `PreToolUse`, blocks the prompt in `UserPromptSubmit`,
  prevents stopping in `Stop`/`SubagentStop`, blocks compaction in `PreCompact`.
* other non-zero — non-blocking error, shown as a `hook error` notice.

Our hooks always exit 0: the curl command ends in `; exit 0`, and `pulse.py ensure-running`
catches every exception and calls `os._exit(0)`.

## Async and HTTP hooks (2026-11-17)

* `"async": true` is a field on `type: "command"` handlers only. Claude Code starts the process and
  continues without waiting; `timeout` is not enforced on it; stdin is the same JSON. Output can
  only feed `additionalContext` / `systemMessage` on the next turn, so we still print nothing.
  The docs give no ordering guarantee, which is why only per-tool events are async (see
  `LATE_EVENT_SECONDS` in `pulse/state.py`).
* `"type": "http"` POSTs the hook JSON to a `url` with no shell at all. A refused connection is a
  non-blocking error and shows a `hook error` notice, so it is noisy whenever Pulse is not
  running. Not used for that reason.

## Status line (used since 2026-11-17)

`pulse.py statusline` reads the status line JSON on stdin. It has
`context_window.context_window_size` (200000 by default, 1000000 for extended-context models) and
`used_percentage`, computed as `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`
(output tokens excluded). `used_percentage` may be `null` early in a session and right after
`/compact`. On Windows the command runs through Git Bash or PowerShell, so paths use forward
slashes. This replaced the transcript tailer and its guess of the window size.
