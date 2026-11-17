"""Branch attribution for Claude sessions that work in a Git worktree they created.

Claude Code stamps every trace row with the branch of the session's own working
directory. A session that runs ``git worktree add <path> -b <branch>`` and then works in
that path through commands or file tools never moves its working directory, so every
row names the original checkout's branch. This module reads main-thread tool inputs in
memory to find the worktrees a session created and which tool calls targeted them.
Only a branch name leaves this module; paths and commands are never stored or returned.
"""

import os
import re
import shlex

from token_meter.domain.branches import dominant_branch, normalize_branch

PATH_KEYS = ("file_path", "notebook_path", "path")
# Tools that act on the filesystem. Planning, messaging, and subagent calls do not say
# where the work happened, so they do not vote.
WORKSPACE_TOOLS = frozenset(("Bash", "Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob"))
MAX_COMMAND_CHARS = 20_000
MAX_TOKENS = 2_000
_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_GIT_OPTIONS_WITH_VALUE = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace"))
_ADD_OPTIONS_WITH_VALUE = frozenset(("-b", "-B", "--orphan", "--reason"))


def _tokens(text):
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        words = text.split()
    return words[:MAX_TOKENS]


def _resolve(base, path):
    if not isinstance(path, str) or not path:
        return ""
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path)) if base else ""


def _created_worktrees(command, cwd):
    """Yield ``(path, branch)`` for each ``git worktree add ... -b|-B <branch> <path>``."""
    here = cwd
    for segment in _SEGMENT.split(command):
        words = _tokens(segment)
        if len(words) >= 2 and words[0] == "cd":
            here = _resolve(here, words[1])
            continue
        if not words or words[0] != "git":
            continue
        base, index = here, 1
        while index < len(words) and words[index].startswith("-"):
            if words[index] == "-C" and index + 1 < len(words):
                base = _resolve(base, words[index + 1])
            index += 2 if words[index] in _GIT_OPTIONS_WITH_VALUE else 1
        if words[index:index + 2] != ["worktree", "add"]:
            continue
        branch, positional, rest, position = "", [], words[index + 2:], 0
        while position < len(rest):
            word = rest[position]
            if word in _ADD_OPTIONS_WITH_VALUE and position + 1 < len(rest):
                if word in ("-b", "-B"):
                    branch = rest[position + 1]
                position += 2
                continue
            if not word.startswith("-"):
                positional.append(word)
            position += 1
        branch = normalize_branch(branch)
        path = _resolve(base, positional[0]) if positional else ""
        if branch and path:
            yield path, branch


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _targeted_branch(name, payload, cwd, worktrees):
    """Branch of the created worktree a tool call works in, or ""."""
    if name == "Bash":
        command = payload.get("command")
        if not isinstance(command, str):
            return ""
        command = command[:MAX_COMMAND_CHARS]
        candidates = [_resolve(cwd, word) for word in _tokens(command)]
    else:
        candidates = [_resolve(cwd, payload.get(key)) for key in PATH_KEYS]
    best = ""
    for root in worktrees:
        hit = any(candidate and _inside(candidate, root) for candidate in candidates)
        if name == "Bash" and not hit:
            hit = root in command
        if hit and len(root) > len(best):
            best = root
    return worktrees[best] if best else ""


def session_branch(rows):
    """Branch a Claude session worked on, from its main-thread trace rows.

    Without tool calls aimed at a worktree the session created, this is the dominant
    recorded ``gitBranch``. Otherwise each filesystem tool call votes: calls inside a
    created worktree vote for that worktree's branch, every other one for its row's
    recorded branch. A worktree branch that wins the vote replaces the recorded one;
    otherwise the recorded branch stays.
    """
    rows = [row for row in rows if isinstance(row, dict)]
    recorded = dominant_branch(row.get("gitBranch") for row in rows)
    worktrees, votes, targeted = {}, [], set()
    for row in rows:
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else ""
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name not in WORKSPACE_TOOLS:
                continue
            payload = block.get("input") if isinstance(block.get("input"), dict) else {}
            command = payload.get("command")
            if name == "Bash" and isinstance(command, str):
                for path, branch in _created_worktrees(command[:MAX_COMMAND_CHARS], cwd):
                    worktrees[path] = branch
            branch = _targeted_branch(name, payload, cwd, worktrees) if worktrees else ""
            if branch:
                targeted.add(branch)
            votes.append(branch or row.get("gitBranch"))
    winner = dominant_branch(votes) if targeted else ""
    return winner if winner in targeted else recorded
