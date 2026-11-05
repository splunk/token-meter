# Token Meter skills

Token Meter ships a small package of agent skills. They teach Claude Code and Codex how
to answer common questions with the local `tokenmeter` MCP: where usage went, whether
spend is on track, and which parts of your setup go unused. Every answer uses numbers
only. No prompts, file contents, or session titles are involved. A fourth skill,
`/token-meter-debug`, helps when Token Meter itself misbehaves. It reads Token Meter's
own install, logs, and local endpoints, and works without the MCP.

## Turn them on

1. Open the Token Meter dashboard, then **Settings → Agent connection**.
2. Click **Connect** for your client's MCP if it is not connected yet. The skills read
   their numbers through it.
3. In the same card, find the **Token Meter skills** row and click **Install**.
4. Start a new agent session. New sessions load the skills; open ones do not.

Codex skills go to `~/.codex/skills`, and Claude Code skills to `~/.claude/skills`. The
Claude desktop app does not load skills from files, so only the MCP applies there.

**Update** appears when a newer version ships. **Remove** deletes Token Meter's copies.

## The skills

| Skill | Ask your agent | What you get |
| --- | --- | --- |
| `token-meter-usage-review` | "Review my agent usage" | A dense report on the last 30 days. Up to five learnings, ranked by estimated cost impact. Each has the evidence, a likely cause, one change to try, and a query to check it in a week. It ends with pattern tables and the evidence Token Meter could not see. |
| `token-meter-budget-pacing` | "Am I on track this month?" | Month-to-date spend, daily run rate, projected month total, and what is left per day. Also the biggest drivers and one move to stay under budget. It changes a cap only when you ask, and confirms the scope and amount with you first. |
| `token-meter-skill-hygiene` | "Which skills and MCP servers do I actually use?" | Installed skill packs with no observed use, and MCP servers ranked by calls and output size. A short list to review. It never changes your configuration. |
| `token-meter-debug` | `/token-meter-debug` in Claude Code, or "Token Meter isn't working" | First asks what you want: a diagnosis, a shareable GitHub issue, or a fix with a pull request. It then checks the install, logs, services, and local endpoints, and asks you questions along the way. Diagnosis is read-only, and trace contents are never opened. Issue and pull request text is sanitized and shown to you first. Nothing is posted until you say yes. |

## What they will and won't do

- **Costs are estimates.** Token Meter uses public prices unless you set your own
  rates. When a total is partial, the skill says so.
- **Missing evidence is shown, never zeroed.** Missing evidence gets its own section in
  each report.
- **No private text.** Reports never include prompts, tool arguments or results, session
  titles, project names, file paths, or session ids.
- **Your own files are safe.** Token Meter changes only skill files that exactly match a
  version it shipped. If you edit a copy, or create a skill with the same name, Token
  Meter never changes or deletes it.

## Adding a skill (for contributors)

- Put each skill in its own folder named `token-meter-<name>`, as `SKILL.md`, with
  `name` and `description` front matter.
- Add the `<!-- token-meter-managed-skill: ... -->` marker line after the front matter.
- Add the sha256 of the file to `RELEASED_SKILL_HASHES` in
  `token_meter/services/agent_skills.py`. When you change a skill, add the new hash and
  keep the old ones, so existing unedited installs update.
- Run `python3 -m unittest tests.test_agent_skills`.
