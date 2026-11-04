---
name: token-meter-skill-hygiene
description: Find installed skill packs and MCP servers that go unused, using the Token Meter MCP, and suggest which to review or disable. Use when the user asks which skills or MCP servers they actually use, wants to trim their agent setup, or asks why every session starts with a large context.
---

<!-- token-meter-managed-skill: installed from the Token Meter skill package. Token Meter updates this file; remove it from Settings, Agent connection. -->

# Token Meter skill and MCP hygiene

Use the `tokenmeter` MCP tools to show which installed skill packs and MCP servers the
user's agents actually use, and which ones only take up context. This skill only reports;
it never changes any configuration. If the `tokenmeter` tools are unavailable, say so and
stop.

## Gather evidence

- Call the tools one at a time; if a call times out, wait a minute and retry once.
- Skill packs: `capabilities` with scope `all` and limit 5, then scope `current`. It lists
  user-installed skill packs with no observed use.
- MCP tool use over the last 30 days: `stats` with `start` 30 days before now, grouped by
  `tool_name`, metrics `tool_calls` and `tool_result_tokens`, sorted by `tool_calls`,
  limit 100. Follow `page.next_cursor` when `page.truncated` is true. Group MCP tools
  by server from their names: `mcp__<server>__<tool>` (Claude Code, Codex) or
  `mcp-<server>-<tool>` (Cursor). Codex also names some of its own built-in app tools
  `mcp__<namespace>__...`; when a "server" appears only in Codex and is not one the
  user installed, label it as a possible built-in rather than a user server.
- Per runtime, repeat that query with the `runtime` filter, because the same server can
  be busy in one client and idle in another.
- The MCP does not list configured MCP servers that were never called, or how many
  definition tokens each one adds. Say so, and point the user to the dashboard's Tools
  page, which shows enabled-but-unused groups and their measured overhead.

## Report

1. **Unused skill packs**: each name from `capabilities`, its runtime, and "no observed
   use".
2. **MCP servers by use**: a small table of server, calls, result tokens, and result
   tokens per call, busiest first.
3. **Quiet servers**: servers with very few calls in 30 days. Flag them for review, not
   removal, because a rarely used server can still matter.
4. **Heavy servers**: servers whose result tokens per call stand out. These grow context
   every time they run.
5. **Suggested review list**: at most five items, each with the evidence and the exact
   place to disable it (the client's own settings, or the dashboard's Tools page).
6. **Missing evidence**: configured-but-never-called servers, definition-token overhead,
   and any runtime where tool evidence is unavailable.

## Rules

- Never disable, remove, or edit anything; the user decides.
- Report observed use only. "No observed use" is not proof that something is unneeded.
- Never include prompts, tool arguments, tool results, session titles, project names,
  file paths, or session ids.
