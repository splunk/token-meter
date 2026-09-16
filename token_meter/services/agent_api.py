"""Read-only agent API service used by MCP."""


AGENT_TOOL_ARGUMENTS = {
    "check": frozenset({"focus", "execution", "session_id"}),
    "usage": frozenset({"window", "focus"}),
    "capabilities": frozenset({"scope", "limit"}),
    "sessions": frozenset({
        "scope", "runtime", "client", "model", "state", "start", "end",
        "cursor", "limit",
    }),
    "trace": frozenset({
        "session_id", "view", "sections", "execution", "event_types", "cursor",
        "limit",
    }),
    "stats": frozenset({
        "metrics", "group_by", "runtime", "client", "model", "state",
        "session_id", "start", "end", "sort_by", "sort_direction", "cursor", "limit",
    }),
    "goal": frozenset({"focus"}),
    "schema": frozenset({"subject", "runtime"}),
}
CALLER_AWARE_AGENT_TOOLS = frozenset({"check", "capabilities", "sessions"})


def validate_agent_tool(name, arguments):
    """Validate a public tool request without invoking its backing service."""
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    allowed = AGENT_TOOL_ARGUMENTS.get(name)
    if allowed is None:
        raise ValueError("Unknown tool: {}".format(name))
    if set(arguments) - allowed:
        raise ValueError("{} received an unsupported argument".format(name))
    return dict(arguments)


def dispatch_agent_tool(service, name, arguments, caller=None):
    """Invoke one allowlisted Agent API tool with its public arguments only."""
    values = validate_agent_tool(name, arguments)
    if name in CALLER_AWARE_AGENT_TOOLS:
        values["caller"] = caller or {}
    return getattr(service, name)(**values)


class AgentAPIService:
    def __init__(self, check, usage, capabilities, goal, queries):
        self._check = check
        self._usage = usage
        self._capabilities = capabilities
        self._goal = goal
        self._queries = queries

    def check(self, **arguments):
        return self._check(**arguments)

    def usage(self, **arguments):
        return self._usage(**arguments)

    def capabilities(self, **arguments):
        return self._capabilities(**arguments)

    def goal(self, **arguments):
        return self._goal(**arguments)

    def sessions(self, **arguments):
        return self._queries.sessions(**arguments)

    def trace(self, **arguments):
        return self._queries.trace(**arguments)

    def stats(self, **arguments):
        return self._queries.stats(**arguments)

    def schema(self, **arguments):
        return self._queries.schema(**arguments)
