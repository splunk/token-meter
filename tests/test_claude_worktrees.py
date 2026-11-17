import time
import unittest

from token_meter.runtimes import claude_worktrees as W


def call(index, name, payload, branch="main", cwd="/src/repo"):
    return {
        "gitBranch": branch, "cwd": cwd,
        "message": {"id": "m{}".format(index), "content": [
            {"type": "tool_use", "id": "t{}".format(index), "name": name, "input": payload}]},
    }


def bash(index, command, **kwargs):
    return call(index, "Bash", {"command": command}, **kwargs)


def created(command, cwd="/src/repo"):
    return list(W._created_worktrees(command, cwd))


class CreatedWorktreeParsingTests(unittest.TestCase):
    def test_paths_resolve_against_cwd_cd_and_git_dash_c(self):
        self.assertEqual(created("git worktree add ../wt -b fix/a origin/main"), [("/src/wt", "fix/a")])
        self.assertEqual(created("cd /other && git worktree add wt -b fix/a"), [("/other/wt", "fix/a")])
        self.assertEqual(created("git -C ../repo2 worktree add ../wt -b fix/a"), [("/src/wt", "fix/a")])
        self.assertEqual(created("(cd /other && git worktree add wt -b fix/a)"), [("/other/wt", "fix/a")])
        self.assertEqual(created('git worktree add "../my wt" -b fix/a'), [("/src/my wt", "fix/a")])

    def test_option_forms(self):
        self.assertEqual(created("git worktree add -bfix/a ../wt"), [("/src/wt", "fix/a")])
        self.assertEqual(created("git worktree add -B fix/a ../wt"), [("/src/wt", "fix/a")])
        self.assertEqual(created("git worktree add --orphan -b fix/a ../wt"), [("/src/wt", "fix/a")])
        self.assertEqual(created("GIT_TRACE=0 git worktree add ../wt -b fix/a"), [("/src/wt", "fix/a")])
        self.assertEqual(created("git --config-env x=Y worktree add ../wt -b fix/a"), [("/src/wt", "fix/a")])
        self.assertEqual(created("git worktree add --lock --reason why ../wt -b fix/a"), [("/src/wt", "fix/a")])

    def test_rejects_shell_text_and_invalid_ref_names(self):
        for command in (
            'git worktree add ../wt -b "$B"',
            "git worktree add ../wt -b $(cat secret|base64)",
            "git worktree add ../wt -b `id`",
            "git worktree add ../wt -b --evil",
            "git worktree add ../wt -b a..b",
            "git worktree add ../wt -b x.lock",
            'git worktree add "$WT" -b fix/a',
            "git worktree add ../wt origin/main",
            "git worktree list",
        ):
            self.assertEqual(created(command), [], command)

    def test_relative_path_without_cwd_is_ignored(self):
        self.assertEqual(created("git worktree add ../wt -b fix/a", cwd=""), [])


class SessionBranchVoteTests(unittest.TestCase):
    def test_sibling_path_with_shared_prefix_does_not_count_as_the_worktree(self):
        rows = [bash(1, "git worktree add /a/wt -b wt/x"),
                *(bash(2 + index, "ls /a/wt2/src && cat /a/wt-old/f") for index in range(5))]
        self.assertEqual(W.session_branch(rows), "main")

    def test_read_and_grep_paths_and_relative_tokens_target_the_worktree(self):
        rows = [bash(1, "git worktree add ../wt -b wt/x"),
                call(2, "Read", {"file_path": "/src/wt/a.py"}),
                call(3, "Grep", {"pattern": "x", "path": "../wt"}),
                bash(4, "python3 ../wt/run.py"),
                bash(5, "ls")]
        self.assertEqual(W.session_branch(rows), "wt/x")

    def test_nested_worktree_takes_the_longest_root(self):
        rows = [bash(1, "git worktree add /a/wt -b outer/x"),
                bash(2, "git worktree add /a/wt/inner -b inner/x"),
                *(call(3 + index, "Edit", {"file_path": "/a/wt/inner/f"}) for index in range(3))]
        self.assertEqual(W.session_branch(rows), "inner/x")

    def test_tie_goes_to_the_branch_voted_latest(self):
        rows = [bash(1, "git worktree add ../wt -b wt/x"), bash(2, "make")]
        self.assertEqual(W.session_branch(rows), "main")
        rows = [bash(1, "make"), bash(2, "git worktree add ../wt -b wt/x")]
        self.assertEqual(W.session_branch(rows), "wt/x")

    def test_malformed_rows_fall_back_to_recorded_branch(self):
        rows = [None, 5, {"message": "x"}, {"message": {"content": [None, {"type": "tool_use", "input": 5}]}},
                call(1, "Bash", {"command": None}), call(2, "Bash", {"command": "echo 'unbalanced"})]
        self.assertEqual(W.session_branch(rows), "main")

    def test_long_unrelated_commands_stay_cheap(self):
        heredoc = "cat <<'EOF' > f\n" + "\n".join("line {} ; a | b".format(n) for n in range(4000)) + "\nEOF"
        rows = [bash(1, "git worktree add ../wt -b wt/x"),
                *(bash(2 + index, heredoc) for index in range(300))]
        started = time.perf_counter()
        self.assertEqual(W.session_branch(rows), "main")
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
