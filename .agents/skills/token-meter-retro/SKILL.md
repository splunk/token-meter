---
name: token-meter-retro
description: Use at the end of every Token Meter feature, bug fix, or design change to log what went wrong to learnings.md, and when the user says "analyse learnings.md" to turn repeated patterns into rules, tests, hooks, interview questions, design-guide entries, or skills.
---

# Retro and learnings log

`specs/plans/learnings.md` is a raw, git-ignored log of what went wrong. Each task adds
at most one line. Once a week, the analysis turns the costliest repeated patterns into
lasting fixes.

## Part A: Task retro (end of every task)

Run after a feature, bug fix, or design change is done, before the final message.
Skip it for questions, reviews, and investigations that changed nothing.

### Step 1: Look back

Compare what happened with the confirmed spec and done criteria. Look for rework: a
user correction, a failed test or gate, a redone design, a wrong first diagnosis, a
tool or environment that blocked progress.

### Step 2: Pick the main slip

Choose the one slip that cost the most time and name its category:

- spec gap: something the interview should have asked
- UI bug: a visual or interaction defect that shipped or nearly shipped
- design drift: work that departed from `specs/DESIGN.md` or the existing design
- wrong assumption: a belief about code, data, or intent that was false
- tooling: scripts, tests, install, agents, or the environment got in the way

If nothing meaningful went wrong, log nothing and stop. Do not invent entries.

### Step 3: Log it

Append to `specs/plans/learnings.md` (create it with the header below if missing):

```markdown
# Learnings log

Date | Task | What went wrong | Category | Time lost
--- | --- | --- | --- | ---
```

- Date | Task | What went wrong | Category (spec gap / UI bug / design drift / wrong assumption / tooling) | Time lost (rough)

Write the row as `2026-11-27 | Spend chart tooltip | Assumed totals included cache reads | wrong assumption | ~20m`.

- Keep each field short. Write time lost in minutes (`~20m`) so it can be summed.
- If an existing rule covered the slip, start "What went wrong" with
  `Ignored rule:` and name it. That is how ignored rules get found.
- Paraphrase only. Never paste prompts, responses, trace content, file contents,
  credentials, or account data into the log.
- Mention the logged line in one sentence of the closing message.

## Part B: Weekly analysis ("analyse learnings.md")

1. Read `specs/plans/learnings.md`. Group entries that describe the same underlying
   problem, and total the time lost for each group.
2. Rank the groups by total time lost. Pick only the top one or two.
3. For each pick, choose the fix:

   | Pattern | Fix |
   |---|---|
   | Happened once | Leave it in the log |
   | Happened twice or more | Add a rule to `specs/AGENTS.md` (the source of `CLAUDE.md` and `AGENTS.md`; mirror it in the root `AGENTS.md` if the generator is unavailable) |
   | `Ignored rule:` keeps appearing | Enforce it with a test under `tests/` or a hook in `.claude/settings.json` or `.codex/hooks.json` |
   | Spec gap | Add a question to `.agents/skills/token-meter-interview/SKILL.md` |
   | Design drift | Add it to `specs/DESIGN.md`, and to `tests/contracts/test_design_system.py` if it can be checked |
   | Repeated workflow | Write a new skill under `.agents/skills/`, with its `.claude/skills/` adapter and an `agent-toolchain.lock.yaml` entry |

4. Show the ranked groups, the proposed fix for the top one or two, and the trend
   (count and time lost per category against previous weeks, using the archive). Say
   plainly whether spec gaps are going down. Apply the fixes after the user agrees.
5. Move the handled entries to `specs/plans/learnings-archive.md` under a
   `## Handled YYYY-MM-DD` heading, noting the fix applied, so `learnings.md` stays
   short. Leave unhandled entries in place.

Fixes to tracked files follow the normal route and approvals; the log and archive are
never staged or committed.
