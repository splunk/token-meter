# Tok Goals Coach Design

## Outcome

Token Meter gains one shared agent named **Tok — Master of tokens** that stays
available while a user moves between Sessions, Spend, Models, Efficiency, Git,
Learn, Tools, and Settings.
Tok turns a natural-language improvement goal into a measurable local
contract, answers questions with Token Meter evidence through MCP, and produces
an optional weekly review through the locally installed Codex CLI.

Goals do not become another top-level dashboard page. Tok is a context lens on
the right side of every existing page: one conversation that can answer a
question, shape a goal, and deliver a weekly review without turning those jobs
into separate navigation modes.

## Persona

Tok is a calm, concise, observant token strategist with a lightly witty voice.
It leads with the strongest evidence-backed signal, states the material caveat,
and ends with one reversible experiment or direct next step. Tok never shames
the user, scores productivity, treats more tokens as inherently bad, or lets
personality outrun available evidence.

The user-facing identity uses an abstract animated token core and orbit in Token
Meter's cyan and mint palette, with a static reduced-motion state. Internal
`coach` routes, package names, stored keys, and the `$token-meter-coach` skill
invocation remain unchanged for compatibility.

## Experience: Tok as a context lens

- A `Tok` button in the sticky header opens the same right-side conversation on
  every route. The closed button may show one quiet goal-attention dot, but it
  never carries a second dashboard of status text.
- On displays wider than 1180 pixels, the panel docks and the dashboard makes
  room for it. From 1024 through 1180 pixels it overlays the right edge so the
  existing dashboard layout does not collapse.
- The open header is one line: Tok's mark and name, the current page label, and
  close. `Master of tokens` belongs in the first-use introduction and accessible
  description instead of a permanent badge. Codex readiness appears only when
  it is unavailable or needs action.
- There are no Chat, Goal, or Weekly tabs. Message content is held only in
  JavaScript memory and disappears on refresh. An active goal appears as one
  thin rail below the composer; activating the rail opens a compact goal detail
  sheet with progress, weekly controls, and a return-to-chat action. A completed
  weekly review enters the conversation as a concise Tok message and remains
  available from the goal detail sheet after refresh.
- The empty conversation uses one short introduction and at most two suggested
  starts. Once the user has sent a message, the introduction disappears.
- Chat receives an allowlisted page context: the current route and, when the
  user is looking at one run, its opaque Token Meter session id. It never
  receives DOM text, project names, paths, trace text, or hidden page data.
- Tok answers with one concise paragraph, one collapsed source line, and at
  most one next action. Evidence is not repeated as a separate table. Expanding
  the source line reveals at most three content-free measurements.
- An answer must name something the user operates, not where usage is
  concentrated. Codex returns an action code from a fixed list of Token Meter
  controls plus an optional named subject it read through MCP; the browser
  generates the label and route locally, so agent text never becomes a control.
  Following it is always a user click and is limited to existing hash routes.
- When Tok reads the current route, the corresponding dashboard section may
  receive one restrained cyan focus outline. It is decorative, never required
  to understand the answer, and clears when the turn finishes or the route
  changes.
- If Codex is missing or not authenticated, the panel still shows deterministic
  goal progress and explains the exact local prerequisite without presenting
  the missing agent result as an empty or zero insight.

## Live turn behavior

Submitting a message appends it immediately and creates exactly one transient
Tok line in the answer position. It starts in the same event loop as submission,
shows an aria-hidden elapsed timer, and changes only when the client or run
crosses an observed boundary:

1. `Starting Tok` after the browser accepts the non-empty submission;
2. `Opening Codex` after the isolated child starts;
3. `Reading Token Meter` when an allowlisted MCP tool begins;
4. `Checking the evidence` after that MCP tool completes;
5. replacement by the final answer after schema and MCP-evidence validation.

Stage 3 names the read-only MCP tool that started, drawn from the shared tool
allowlist, so `Reading your usage history` replaces the generic label when that
tool is observed. A secondary line reports the number of observed tool
completions. Past a fixed elapsed threshold it adds that Tok is still working;
that one statement is explicitly time-derived rather than boundary-derived and
must not name a cause, estimate a duration, or imply provider latency.

The line uses a small indeterminate orbit as its motion signal, with a static
marker under reduced motion. It never shows a fake percentage, simulated
checklist, model reasoning, raw event text, tool arguments, or tool results.
Failures are shown, not only announced: an undelivered turn becomes a distinct
error entry with a retry control, and an unmet local prerequisite becomes a
notice naming it. `aria-live=polite` announces stage changes but
not timer ticks; reduced-motion users receive a static state marker.

The textarea stays enabled so the user can draft a follow-up. The send control
becomes a stop control after eight seconds. Stopping terminates only the active
ephemeral Coach child, preserves the user's message, and replaces the transient
line with `Stopped` plus one retry action. Closing Tok does not cancel the turn;
reopening it shows the current stage or completed answer.

## Goal contract

V1 deliberately supports one active goal. This keeps progress legible and
prevents several optimization targets from quietly fighting each other.

Codex may draft only these dimensions:

- metric: `cost_per_execution`, `output_per_dollar`, `wait_per_execution`,
  `retry_rate`, `tool_tokens_per_execution`, or `context_peak`
- direction: derived from the metric and never supplied independently
- target percent: integer from 5 through 80
- evidence window: 7, 14, or 30 days
- runtime scope: `all`, `claude`, `codex`, `cursor`, `opencode`, `kiro`, `pi`,
  or `hermes`
- weekly review weekday: integer 0 through 6, where 0 is Monday
- weekly review enabled: explicit Boolean shown before activation

The stored goal contains only those values, generated timestamps, its baseline
and latest numeric snapshots, and last-review metadata. The natural-language message and
Codex wording are never copied into settings. Display labels are generated from
allowlisted local templates.

Progress is evidence bounded. A value is available only if every component
needed by its formula has covered observations:

| Metric | Formula | Better direction |
|---|---|---|
| Cost per execution | covered cost / covered executions | down |
| Output per dollar | covered output tokens / covered cost | up |
| Wait per execution | covered wait seconds / covered executions | down |
| Retry rate | covered retries / covered attempts | down |
| Tool tokens per execution | covered tool-result tokens / covered executions | down |
| Context peak | maximum covered context token count | down |

When evidence is absent or partial, the UI says `Unavailable` or `Partial`; it
never turns missing evidence into zero. Progress uses the saved baseline and is
clamped to 0-100 percent of the requested relative improvement.

## Agent boundary

Tok invokes the user's local Codex CLI; Token Meter does not embed an API
key, model id, or provider credential. Each invocation:

1. resolves the Codex executable with the existing LaunchAgent-safe lookup;
2. creates a temporary, non-repository workspace containing only the bundled
   `token-meter-coach` skill and an ephemeral Codex home linked only to the
   user's existing saved authentication;
3. runs `codex exec` with `--ephemeral`, `--sandbox read-only`,
   `--ignore-user-config`, `--ignore-rules`, `--skip-git-repo-check`, JSONL
   events, and a strict JSON output schema;
4. injects exactly one required stdio MCP server named `tokenmeter`, with an
   allowlist of Token Meter's read-only tools;
5. disables general shell, file, browser, app, image, plugin, memory, and
   sub-agent capabilities so only the allowlisted MCP tools are usable;
6. sends only the newly typed Tok message, bounded in-memory conversation
   context, the allowlisted page context, and the structured active goal;
7. consumes JSONL as it arrives, maps only allowlisted event types to the four
   content-free live stages, and retains neither event text nor MCP payloads;
8. caps runtime, input size, event bytes, final output bytes, evidence rows, and
   text lengths; then deletes the temporary workspace;
9. returns a sanitized error code on timeout, auth failure, malformed output,
   missing MCP evidence, or cancellation without returning stderr, paths, or
   raw exceptions to the browser.

The user-visible panel explicitly says that a sent Tok message and the
content-free Token Meter metrics selected through MCP are processed by Codex
under the user's existing OpenAI account. Raw trace content, prompts from agent
traces, responses from agent traces, reasoning, tool arguments/results,
credentials, settings values, filenames, and project names remain excluded.

## Warm MCP evidence path

The current stdio MCP process imports quickly but builds historical usage in a
separate application instance, forcing a full trace rescan even while the Token
Meter dashboard server already holds a warm discovery snapshot. Coach-launched
MCP processes therefore receive a loopback URL for one bounded internal query
route.

- The stdio MCP contract presented to Codex remains unchanged. Codex still
  chooses and calls an allowlisted Token Meter MCP tool.
- For a Coach caller, the MCP process posts the tool name and validated bounded
  arguments to the running local server. The server dispatches through the same
  shared tool-name and argument allowlist as stdio MCP, uses its warm
  `AgentAPIService`, and returns only the existing sanitized tool projection.
- The current action token is inherited only by the isolated Coach process and
  its MCP child through the sanitized process environment. It is neither placed
  in command arguments nor model input. The bridge validates it using the same
  header contract as other internal Coach POST routes.
- The route rejects non-JSON, oversized, cross-origin, unknown-tool, and
  out-of-contract requests. It never returns the dashboard's multi-megabyte
  state payload or any raw trace data.
- When loopback is unavailable, times out, or returns an invalid projection,
  stdio MCP falls back once to its existing local read path. The answer remains
  correct even when the speed optimization is unavailable.
- The bridge exists only for Coach-launched MCP. Existing user-configured Codex
  and Claude MCP entries keep their current behavior and configuration.

This removes the avoidable second trace scan without replacing Codex, bypassing
MCP, weakening evidence validation, or promising a provider-response SLA.

## Structured responses

Interactive Chat output is ephemeral and contains bounded plain text,
content-free evidence rows, at most one optional coded action, and an optional
goal draft. The schema no longer asks Codex to generate a stack
of suggested prompts. The browser renders with `textContent`; agent output is
never treated as HTML. When a goal draft is present, its activation control is
the single next action and any coded action is suppressed.

Weekly output is narrower. The agent chooses one recommendation code from:

- `keep_course`
- `collect_more_data`
- `test_lower_cost_model`
- `reduce_retries`
- `reduce_tool_output`
- `reduce_wait`
- `reduce_context`

Token Meter persists the code and the numeric comparison snapshot, not the
agent's prose. The goal detail sheet renders a local weekly template, the exact
measurements, coverage, last run, next run, and a `Run now` action. This makes
background weekly reviews useful after a refresh without building a
prompt/response log.

## Scheduling and concurrency

- Automatic weekly analysis is opt-in on the activation card and can be paused
  from the goal detail sheet.
- The existing Token Meter server owns a lightweight scheduler. It checks once
  per minute, runs only after the selected local weekday begins, and records at
  most one completed review per local ISO week.
- Goal activation stores an unavailable placeholder immediately. The scheduler
  collects and caches baseline/current numeric evidence in the background for
  15 minutes, so activation and state reads never wait on a trace scan.
- Goal-state read-modify-write phases are serialized, and the shared settings
  writer serializes Coach changes with other machine-wide settings. A refresh
  re-reads the latest goal before merging its snapshot, preserving a concurrent
  weekly toggle and discarding evidence from a replaced goal revision.
- A process-wide nonblocking lock prevents interactive and scheduled Codex runs
  from overlapping. Service construction is also serialized so the scheduler
  and an early HTTP request cannot create independent locks. A second request
  gets a stable `busy` response. The executor exposes only the active stage,
  start time, and cancellability through Coach state.
- Failure records only a bounded error code and attempt time. Failed runs may be
  retried manually and do not replace the last successful structured review.
  An automatic failure is not retried again in the same week, preventing a
  one-minute failure loop from consuming repeated Codex runs.
- Cancellation and server shutdown terminate no unrelated Codex process. The
  bounded invocation owns its exact child handle, requests termination, waits a
  short grace period, and kills only that child if it does not exit. The child
  handle and cancellation flag are cleared in `finally` on success and every
  failure path.
- A cancellation arriving after the child has already completed is idempotent:
  it reports that nothing changed and never targets a later run.

## HTTP and MCP surfaces

- `GET /coach/state`: agent availability, active content-free stage and start
  time, cancellability, active structured goal, deterministic progress, last
  structured weekly review, next due time, and action token.
- `POST /coach/ask`: ephemeral chat request and structured reply.
- `POST /coach/cancel`: stop only the active ephemeral Coach child.
- `POST /coach/evidence`: Coach-MCP-only bounded query through the running
  server's warm read-only `AgentAPIService`, with no dashboard state response.
- `POST /coach/goal`: activate, pause weekly review, or clear the active goal.
- `POST /coach/weekly`: run a due or user-requested weekly review.
- All POST routes keep the existing local-origin, JSON, body-size, and
  `X-Token-Meter-Action` checks.
- MCP adds read-only `goal` with `focus=active|progress|weekly`. Its response is
  the same bounded structured projection and includes no natural-language
  source message or agent response.

## Acceptance criteria

1. The same Tok panel and in-memory conversation remain present across every
   supported dashboard route.
2. A natural-language request can produce a validated goal draft; activation
   persists only the structured contract and begins evidence-correct baseline
   collection asynchronously.
3. Goal progress distinguishes complete, partial, and unavailable evidence.
4. Agent runs are Codex-only, ephemeral, read-only, schema constrained, use a
   required tool-allowlisted Token Meter MCP server, and expose sanitized errors.
5. Weekly analysis runs at most once per local week, can be triggered manually,
   and persists only a recommendation code plus numeric evidence.
6. Internal Coach POST routes reject cross-origin, unauthenticated, oversized, malformed,
   or out-of-contract requests.
7. The panel is keyboard accessible, announces busy/error changes, returns
   focus on close, makes the background inert only while it overlays the
   1024-pixel layout, and docks without blocking the wide desktop.
8. The staged runtime includes the Coach service and skill, passes source/runtime
   parity, serves healthy endpoints, and leaves the native menu-bar payload
   valid.
9. A submitted message receives visible in-stream feedback immediately; stage
   copy changes only from observed child/MCP boundaries, timer ticks are not
   announced, and reduced motion remains readable.
10. The default conversation has no mode tabs, persistent readiness badges,
    expanded evidence table, or multi-prompt action stack; an answer renders one
    collapsed source line and at most one action.
11. Coach-launched MCP uses the warm evidence route when it is healthy and
    falls back once to the existing local read when it is not. Tool dispatch and
    privacy allowlists are shared so transport choice cannot expand data access;
    a warm-route test proves the fallback discovery path is not invoked.
12. The active Coach child can be stopped without affecting unrelated Codex or
    MCP processes; cancellation, timeout, success, and failure all clear live
    state and child ownership.
