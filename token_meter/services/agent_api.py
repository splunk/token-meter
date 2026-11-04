"""Bounded agent API service used by MCP."""


class AgentAPIService:
    def __init__(self, check, usage, capabilities, queries, budget,
                 set_session_budget, set_default_session_budget,
                 git_stats=None, work_stats=None):
        self._check = check
        self._usage = usage
        self._capabilities = capabilities
        self._queries = queries
        self._budget = budget
        self._set_session_budget = set_session_budget
        self._set_default_session_budget = set_default_session_budget
        self._git_stats = git_stats
        self._work_stats = work_stats

    def check(self, **arguments):
        return self._check(**arguments)

    def usage(self, **arguments):
        return self._usage(**arguments)

    def capabilities(self, **arguments):
        return self._capabilities(**arguments)

    def budget(self, **arguments):
        return self._budget(**arguments)

    def set_session_budget(self, **arguments):
        return self._set_session_budget(**arguments)

    def set_default_session_budget(self, **arguments):
        return self._set_default_session_budget(**arguments)

    def git_stats(self, **arguments):
        return self._git_stats(**arguments)

    def work_stats(self, **arguments):
        return self._work_stats(**arguments)

    def sessions(self, **arguments):
        return self._queries.sessions(**arguments)

    def trace(self, **arguments):
        return self._queries.trace(**arguments)

    def stats(self, **arguments):
        return self._queries.stats(**arguments)

    def schema(self, **arguments):
        return self._queries.schema(**arguments)
