"""Turn hook payload pieces into short, content-free summaries.

Nothing in here may return file contents, prompts or full commands. The only things kept are
the tool name, a file basename, or the first ~40 characters of a shell command.
"""
import ntpath
import posixpath

SHELL_TOOLS = {"Bash", "PowerShell"}
PATH_KEYS = ("file_path", "notebook_path", "path")
MAX_CMD = 40


def short_tool(name):
    """mcp__github__create_issue -> create_issue; keeps ECG labels tiny."""
    name = str(name or "tool")
    if name.startswith("mcp__"):
        parts = name.split("__")
        name = parts[-1] or name
    return name[:14]


def basename(p):
    p = str(p).rstrip("/\\")
    return posixpath.basename(ntpath.basename(p)) or p


def one_line(text, limit=MAX_CMD):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize(tool_name, tool_input):
    """Return e.g. 'app.py', 'npm test', or '' — never content."""
    if not isinstance(tool_input, dict):
        return ""
    if tool_name in SHELL_TOOLS and tool_input.get("command"):
        return one_line(tool_input["command"])
    for key in PATH_KEYS:
        if tool_input.get(key):
            return basename(tool_input[key])
    if tool_name in ("Agent", "Task") and tool_input.get("subagent_type"):
        return one_line(tool_input["subagent_type"], 24)
    return ""


def response_failed(tool_response):
    """Best-effort detection of a failed tool from PostToolUse's tool_response."""
    if not isinstance(tool_response, dict):
        return False
    if tool_response.get("interrupted") is True:
        return False
    if tool_response.get("is_error") is True or tool_response.get("isError") is True:
        return True
    for key in ("exit_code", "exitCode", "returncode", "returnCode"):
        val = tool_response.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val != 0:
            return True
    err = tool_response.get("error")
    return bool(err) and err is not False
