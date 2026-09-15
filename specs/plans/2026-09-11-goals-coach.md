# Global Goals Coach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a global right-side Token Meter Coach that converts natural-language optimization goals into measurable contracts and produces evidence-bounded Codex/MCP coaching, including optional weekly reviews.

**Architecture:** A focused `token_meter.coach` package owns goal validation, evidence formulas, persistence projections, safe Codex execution, and scheduling. `token_meter.app` wires those services into four localhost HTTP routes and one read-only MCP tool; `page.html` owns the shared in-memory conversation and responsive panel. The runtime bundles a repo-local Coach skill into a temporary non-repository workspace for every ephemeral invocation.

**Tech Stack:** Python 3.8+ standard library, `unittest`, stdio MCP, Codex CLI non-interactive mode, single-file HTML/CSS/JavaScript dashboard.

**Spec:** `specs/2026-09-11-goals-coach-design.md`

## Global Constraints

- Keep the Python server dependency-free and local-only.
- Keep the top-level dashboard order `Sessions -> Spend -> Models -> Efficiency -> Git -> Learn -> Tools -> Settings`; Goals lives inside Coach.
- Never send trace prompts, responses, reasoning, tool arguments/results, credentials, settings values, paths, project names, or raw traces to Codex.
- Never persist Coach messages or Codex prose; persist only validated goal fields, numeric snapshots, bounded status codes, and allowlisted recommendation codes.
- Treat unavailable evidence as unavailable, never measured zero.
- Use only the user's existing Codex CLI authentication and model selection.
- Require the `tokenmeter` MCP server and allow only read-only Token Meter tools for a Coach run.
- Do not silently change model, skill, MCP, budget, or agent configuration.
- Do not commit, push, or open a pull request without new explicit user approval.

---

### Task 1: Goal contracts, formulas, and atomic settings

**Files:**
- Create: `token_meter/coach/__init__.py`
- Create: `token_meter/coach/contracts.py`
- Create: `token_meter/coach/service.py`
- Create: `tests/test_coach.py`
- Modify: `token_meter/app.py`

**Interfaces:**
- Consumes: `stats(metrics, **filters) -> dict`, existing `load_json` and `atomic_write_text`, wall-clock `now()`.
- Produces: `normalize_goal(value) -> dict`, `metric_snapshot(goal, stats) -> dict`, `goal_progress(goal, snapshot) -> dict`, and `CoachService.state()/save_goal()/clear_goal()`.

- [x] **Step 1: Write failing contract and formula tests**

Add table-driven tests with literal results for every supported metric, target
bounds, runtime/window/weekday validation, zero denominators, partial coverage,
and relative progress direction. Assert that serialized stored state excludes a
sentinel natural-language goal and all unknown input keys.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_coach.GoalContractTests tests.test_coach.GoalEvidenceTests -v`

Expected: import failure because `token_meter.coach.contracts` does not exist.

- [x] **Step 3: Implement contracts and evidence formulas**

Use immutable allowlists for metrics/runtimes/windows/recommendations, finite
numeric validation, and a `coverage` value of `complete`, `partial`, or
`unavailable`. Calculate ratios only from covered numerator/denominator values;
clamp progress to `[0, 100]`.

- [x] **Step 4: Write failing persistence behavior tests**

Exercise a real temporary settings file. Verify migration from missing or
malformed `coach` state, idempotent writes, atomic replacement, baseline capture
at activation, weekly pause updates, clear behavior, and omission of free text.

- [x] **Step 5: Run persistence tests and verify RED**

Run: `python3 -m unittest tests.test_coach.CoachPersistenceTests -v`

Expected: failure because `CoachService` is not implemented.

- [x] **Step 6: Implement minimal persistence and state projection**

Implement `CoachService` with injected settings path, stats callback, and clock.
Reuse Token Meter's atomic JSON writer, preserve unrelated settings keys, and
return only public structured state.

- [x] **Step 7: Run all Task 1 tests and verify GREEN**

Run: `python3 -m unittest tests.test_coach -v`

Expected: all contract, formula, and persistence tests pass.

### Task 2: Safe Codex executor and bundled Coach skill

**Files:**
- Create: `token_meter/coach/codex.py`
- Create: `token_meter/coach/workspace/.agents/skills/token-meter-coach/SKILL.md`
- Create: `token_meter/coach/workspace/.agents/skills/token-meter-coach/agents/openai.yaml`
- Modify: `tests/test_coach.py`

**Interfaces:**
- Consumes: `codex_path`, `mcp_command`, `mcp_args`, request mode, bounded prompt context, JSON schema, and injected process runner.
- Produces: `CodexCoach.status() -> dict` and `CodexCoach.run(request) -> dict` with sanitized `CoachRunError.code` failures.

- [x] **Step 1: Write failing command-boundary and output tests**

Assert with a recording fake process that the command contains `exec`,
`--ephemeral`, `--sandbox read-only`, `--ignore-user-config`, `--ignore-rules`,
`--skip-git-repo-check`, `--json`, `--output-schema`, a required `tokenmeter`
stdio MCP definition, an MCP tool allowlist, and a temporary `-C` directory.
Assert the prompt explicitly invokes `$token-meter-coach`, contains only the
allowlisted page/goal fields, and never embeds the settings path or MCP command.

- [x] **Step 2: Run executor tests and verify RED**

Run: `python3 -m unittest tests.test_coach.CodexCoachTests -v`

Expected: import failure because `token_meter.coach.codex` does not exist.

- [x] **Step 3: Implement bounded ephemeral execution**

Use `TemporaryDirectory`, `shutil.copytree`, `subprocess.Popen.communicate`, a
90-second timeout, a 64 KiB event cap, a 32 KiB result cap, strict mode-specific
schemas, and recursive field validation. Kill only the owned child on timeout.
Map missing CLI, timeout, busy, auth, MCP startup, nonzero exit, oversized output,
malformed JSON, and invalid schema to stable error codes without stderr text.

- [x] **Step 4: Add failing MCP-use and privacy tests**

Feed literal JSONL fixtures for successful required `tokenmeter` tool use,
missing MCP use on weekly runs, adversarial extra result fields, HTML, a private
sentinel, oversized output, timeout, and nonzero exit. Verify temporary files are
removed and no result is written to settings.

- [x] **Step 5: Write the focused Coach skill**

The skill must tell Codex to use only Token Meter MCP evidence, keep unavailable
distinct from zero, avoid causal/quality claims, never change configuration,
return the supplied schema exactly, and prefer one measurable experiment.

- [x] **Step 6: Run Task 2 tests and verify GREEN**

Run: `python3 -m unittest tests.test_coach.CodexCoachTests -v`

Expected: all safe-execution and privacy cases pass.

### Task 3: Chat, goal, weekly HTTP routes and scheduler

**Files:**
- Modify: `token_meter/coach/service.py`
- Modify: `token_meter/app.py`
- Create: `tests/test_coach_http.py`
- Modify: `tests/test_coach.py`

**Interfaces:**
- Consumes: `CoachService`, `CodexCoach`, existing `_ACTION_TOKEN`, existing
  `TokenMeterHTTPServer`, and existing MCP statistic service.
- Produces: `GET /coach/state`, `POST /coach/ask`, `POST /coach/goal`,
  `POST /coach/weekly`, `coach_scheduler()`, and sanitized HTTP JSON responses.

- [x] **Step 1: Write failing real-server HTTP tests**

Start `TokenMeterHTTPServer(("127.0.0.1", 0), H)` for each case. Verify state
shape; valid ask; draft activation; pause; clear; manual review; 404 for unknown
routes; and rejection of foreign Origin, missing/wrong action token, non-JSON,
empty/oversized body, malformed JSON, unsupported keys, oversized conversation,
invalid route/session id, and invalid goal fields.

- [x] **Step 2: Run HTTP tests and verify RED**

Run: `python3 -m unittest tests.test_coach_http -v`

Expected: the new routes return 404.

- [x] **Step 3: Wire the four endpoints**

Factor the existing POST preflight so Coach uses the same local-origin,
content-type, action-token, and request-size rules. Use status 200 for reads and
completed actions, 202 only for an accepted nonblocking run, 400 for validation,
409 for busy, and 503 for CLI/auth/MCP unavailability.

- [x] **Step 4: Write failing scheduler tests**

With an injected local clock and fake executor, prove disabled/no-goal/not-due
are no-ops, a due review runs once, repeated wakes in the same ISO week do not
run again, failures keep the prior report, and a nonblocking lock prevents
overlap between scheduled and interactive runs.

- [x] **Step 5: Implement scheduler and structured weekly storage**

Check once per minute in the existing background-service lifecycle. Store only
the numeric snapshot, recommendation enum, completed/attempt timestamps, and
bounded error code. Render prose later from local templates.

- [x] **Step 6: Run Task 3 tests and verify GREEN**

Run: `python3 -m unittest tests.test_coach tests.test_coach_http -v`

Expected: all service, route, scheduler, and concurrency tests pass.

### Task 4: Read-only goal MCP surface

**Files:**
- Modify: `token_meter/services/agent_api.py`
- Modify: `token_meter_mcp.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_coach.py`

**Interfaces:**
- Consumes: `CoachService.agent_projection(focus) -> dict`.
- Produces: MCP tool `goal` with `focus=active|progress|weekly` and read-only annotations.

- [x] **Step 1: Write failing MCP list/call/privacy tests**

Verify `tools/list` includes `goal`, all three focus values dispatch, unknown
arguments fail, read-only annotations are present, and a serialized response
contains no natural-language sentinel, path, settings key, or Codex prose.

- [x] **Step 2: Run MCP tests and verify RED**

Run: `python3 -m unittest tests.test_mcp_server tests.test_coach -v`

Expected: `goal` is absent from the tool list.

- [x] **Step 3: Add the bounded service and tool adapter**

Expose `AgentAPIService.goal(**arguments)`, add the schema/description/tool
dispatch, increment the MCP version, and update server instructions. The tool
must never invoke Codex and must remain idempotent/read-only.

- [x] **Step 4: Run MCP and privacy tests and verify GREEN**

Run: `python3 -m unittest tests.test_mcp_server tests.test_mcp_queries tests.test_coach -v`

Expected: all MCP contracts pass.

### Task 5: Shared responsive dashboard Coach

**Files:**
- Modify: `page.html`
- Modify: `tests/test_meter.py`
- Create: `tests/test_coach_dashboard.py`

**Interfaces:**
- Consumes: the four HTTP routes, existing hash router, `LATEST` state, and the
  existing action token.
- Produces: one global accessible Coach panel with Chat, Goal, Weekly tabs and
  explicit allowlisted navigation actions.

- [x] **Step 1: Write failing DOM and JavaScript behavior tests**

Use a lightweight Node DOM harness to assert open/close, tab switching,
conversation survival across `applyHashRoute`, no `localStorage` or
`sessionStorage` message writes, route-context allowlisting, safe `textContent`
rendering, explicit route-button navigation, goal activation, busy/error live
regions, Escape close, focus return, and 1024 overlay versus wide dock classes.

- [x] **Step 2: Run dashboard tests and verify RED**

Run: `python3 -m unittest tests.test_coach_dashboard -v`

Expected: Coach DOM and functions are absent.

- [x] **Step 3: Implement the panel shell and state renderer**

Add semantic `aside`, labelled tablist, live region, empty/loading/error states,
message list, composer, goal contract card, progress/coverage display, weekly
card, run/pause/clear controls, and local disclosure. Use existing design tokens,
8-pixel radii, restrained cyan signal, and no hosted assets.

- [x] **Step 4: Implement bounded fetch and navigation behavior**

Keep messages in a module-level array only; send at most six bounded turns.
Render every agent string through `textContent`. Map returned route ids through a
literal object and require a button click before changing the hash.

- [x] **Step 5: Run dashboard tests and embedded-JS parse**

Run: `python3 -m unittest tests.test_coach_dashboard -v`

Run: `node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"`

Expected: dashboard tests pass and output is `js ok`.

### Task 6: Documentation, packaging, installed runtime, and independent gates

**Files:**
- Modify: `README.md`
- Modify: `specs/USER_GUIDE.md`
- Modify: `specs/SECURITY.md`
- Modify: `specs/ARCHITECTURE.md`
- Modify: `specs/plans/active.md` (ignored; never stage)

**Interfaces:**
- Consumes: completed source implementation and validation evidence.
- Produces: honest user/architecture/privacy documentation and a staged runtime matching source.

- [x] **Step 1: Document the exact product and privacy contract**

Explain Codex-only availability, explicit provider processing, local ephemeral
chat, structured goal persistence, weekly opt-in and Codex usage, MCP evidence,
unavailable semantics, and how to pause/clear. Do not claim model quality or
task outcomes are measured.

- [x] **Step 2: Run source gates**

Run focused Coach/MCP/dashboard tests, full `python3 -m unittest discover -s tests -v`,
Python compilation, embedded JavaScript parsing, shell syntax, Swift compilation
and smoke output, `./scripts/check-agent-tools`, runtime-manifest tests, and
`git diff --check`. Record the known baseline README wording failure separately
unless it is changed in scope.

- [x] **Step 3: Run browser verification**

Serve the feature branch, inspect every Coach tab at a wide desktop viewport and
at 1024 pixels, exercise route changes, keyboard focus, goal activation, manual
weekly run, unavailable Codex, long output, busy, and error states. Save
screenshots only outside tracked source.

- [x] **Step 4: Install and verify the exact runtime**

Run `./scripts/install`; verify `/health`, `/menubar`, `/coach/state`, both
LaunchAgent states, source/runtime manifest parity, automatic start, and the
installer's printed uninstall command. Never patch the staged runtime directly.

- [ ] **Step 5: Run independent high-risk gates**

Freeze tracked files, fingerprint base-to-head, dispatch one read-only tester and
two read-only Token Meter reviewers with distinct correctness/privacy and
UX/cross-platform lenses, reject stale handoffs, resolve every finding, and rerun
all invalidated gates after any tracked edit.

- [ ] **Step 6: Stop before external delivery**

Report files, exact tests, runtime evidence, browser evidence, findings, and any
unverified target-host behavior. Do not commit, push, create a pull request, or
post externally without explicit user approval.
