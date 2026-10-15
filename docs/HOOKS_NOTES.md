# Claude Code hooks — what was verified (2026-10-15, Claude Code 2.1.300)

Sources: <https://code.claude.com/docs/en/hooks>, <https://code.claude.com/docs/en/statusline>,
plus real transcripts under `~/.claude/projects/` on this machine.

## Hook events

The docs list 33 events. The ones Claude Pulse uses:

| Event | Used for | Matcher support |
|---|---|---|
| `SessionStart` | create card, auto-start server (`source`: startup/resume/clear/compact/fork) | yes |
| `UserPromptSubmit` | start of a task (we never read `prompt`) | **no** |
| `PreToolUse` | tool spike, `working` | yes (tool name) |
| `PostToolUse` | `working`, error detection in `tool_response` | yes |
| `PostToolUseFailure` | **dedicated tool-failure event** → `error` (`error`, `is_interrupt`) | yes |
| `PermissionRequest` | permission dialog shown → `blocked` | yes |
| `Notification` | `blocked` (`notification_type`: `permission_prompt`, `idle_prompt`, `elicitation_dialog`, `agent_needs_input`, …) | yes |
| `Stop` | `done` | **no** |
| `SubagentStop` | flutter pulse | yes (agent type) |
| `PreCompact` | deep-breath animation, reset context gauge (`trigger`: manual/auto) | yes |
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

## Status line (not used, but relevant)

The status line JSON has `context_window.context_window_size` (200000 by default, 1000000 for
extended-context models) and `used_percentage`, computed as
`input_tokens + cache_creation_input_tokens + cache_read_input_tokens` (output tokens excluded).
Claude Pulse uses the same formula on the transcript.

## Transcript JSONL (verified on this machine)

Assistant lines look like
`{"type":"assistant","isSidechain":false,"message":{"model":…,"content":[{"type":"thinking",…}|{"type":"text"}|{"type":"tool_use"}],"usage":{"input_tokens":2,"cache_creation_input_tokens":2942,"cache_read_input_tokens":62730,"output_tokens":683,…}}}`.
Each content block may be its own line. Lines can be very large (`api-request-blob`), and the
last line may be partially written, so the watcher buffers until a newline arrives.
One real session here reached 515k tokens, i.e. a 1M-context model — so when usage exceeds
`context_window_tokens`, Pulse assumes a 1,000,000-token window.
