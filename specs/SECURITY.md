# Security Policy

The canonical component and privacy-boundary map is
[ARCHITECTURE.md](ARCHITECTURE.md#privacy-and-security-invariants).

## Supported Versions

Token Meter is a small local tool. Security fixes should target the current
`main` branch unless the project later publishes versioned releases.

## Reporting A Vulnerability

Please report security issues privately to the project maintainers rather than
opening a public issue with exploit details.

When reporting, include:

- The affected commit or release.
- Your operating system and Python version.
- Steps to reproduce.
- Any relevant error messages.

Do not attach Claude Code, Codex, or Cursor session data unless you have reviewed
and redacted it. Local transcripts, Cursor's shared state database, and request
logs may contain prompts, responses, project paths, tool outputs, and other
sensitive information.

## Security Model

Token Meter is intended to run on your own machine and bind to `127.0.0.1`.
It should not expose the dashboard to public networks. The project should not
send logs, prompts, responses, project paths, token counts, or costs to external
services.

Work insights are opt-in. When enabled, typed user-turn text and the last 600
characters of the preceding assistant reply are held only in a bounded
in-memory queue and sent only to a loopback Ollama endpoint (`127.0.0.1` or
`::1`; `localhost` is stored as `127.0.0.1`; plain HTTP; redirects refused).
Ollama models that report a remote host or a cloud tag are refused; the local model entry is re-checked before every model request. A user who re-points the model name while a request is already in flight, or forwards the loopback port elsewhere, is outside this guarantee. The label
ledger stores salted keys, enum labels, confidences, and reason codes; it never
stores text. "Delete all labels" removes the ledger and rotates its salt.

The Git page is a local-only Git reader. It inspects bounded remote-tracking reflogs
for successful-push markers and never fetches, pulls, pushes, updates refs, or
contacts a remote. Its SQLite ledger stores salted repository/object keys,
timestamped clear baselines, numeric text-line totals, and coverage state only.
Public project suffixes use the same per-machine salt. It must not
persist or project paths, remote/ref names, identities, messages, filenames,
diff content, raw object IDs, subprocess output, or exceptions.
Runtime-specific provider resources are not automatically safe public model
identifiers. For example, a Pi application-profile reference can contain
account-bearing segments. Such a value must never be projected verbatim or used
to infer a priced foundation model; it is replaced with a non-account generic
label before dashboard, native, or MCP projection.

The menu-bar quota view is a bounded exception to otherwise local processing.
It makes read-only account-usage requests to the provider matching the local
credential: Codex through its local app-server or signed-in usage API, Claude
through Anthropic's OAuth usage endpoint for first-party OAuth accounts, and
Cursor through its account usage summary. It must never send a provider token
to a different provider, persist copied credentials, expose credentials or raw
response bodies through localhost, or include them in logs and errors. Provider
requests use fixed HTTPS endpoints, hard timeouts, bounded response sizes, and
sanitized failures. Third-party Claude auth must fail closed as unavailable.

The optional `tokenmeter` MCP server is local and uses stdio; it does not open
another listening port. Its evidence tools return bounded derived evidence. It
must never return prompts, messages, reasoning text, tool arguments, tool
results, credentials, environment variables, unrelated configuration values, or log
paths. `check` detail is limited to the caller's matched current runtime and
project. Query tools may return opaque session IDs and content-free historical
evidence, but never session titles, project names, source paths, or native
provider payloads. Capability names are returned only when capability review is
explicitly requested.

The `sessions`, `trace`, `stats`, and `schema` tools use strict input schemas,
positive output allowlists, and fixed limits. Session IDs identify only an
already discovered local source. Pagination cursors contain hashed query and
revision bindings, not paths or trace content; a changed revision invalidates
the cursor. Serialized query pages are capped at 65,536 bytes. The
`native_structure` view is not raw trace content: it admits only fixed native
type/subtype enums plus bounded numeric, status, model, and tool fields. Raw
prompts, responses, tool payloads, account data, and trace paths are not
available through MCP.

When an MCP tool is called, the bounded derived result is handed to the
connected Codex or Claude client. That client may send the result to its model
provider under the client's own terms and configuration. “Local analysis” means
Token Meter does not upload trace content or derived analytics; it does not mean
data returned to an explicitly connected AI client necessarily remains on the
machine. The separate quota requests above contain authentication and ordinary
provider usage-request metadata, but no local transcript content.

Dashboard connection actions are protected by the existing local-origin action
token and fixed subprocess argument vectors. They may add or remove only the
exact user-level MCP entry named `tokenmeter`, must refuse a conflicting entry,
and must verify the persisted state before reporting success. The MCP tools
themselves cannot change configuration, budgets, sessions, or Token Meter state,
except for the narrowly scoped `set_session_budget` and
`set_default_session_budget` tools. Those setters require `confirm: true`,
accept only a validated USD cap, can compare against an expected current cap,
and write only the atomically persisted session-cap/default-cap settings. They
cannot alter monthly allocations, pricing, connections, traces, or sessions.

Dashboard session deletion uses the same local-origin action token and accepts
only a canonical ID from the currently discovered session inventory. It moves
the exact discovered `.jsonl` file to the system Trash with collision-safe naming;
it does not delete provider metadata, project files, configuration, Cursor's
shared `state.vscdb` database/WAL, or Cursor request logs. The UI
requires an explicit confirmation and warns when the target appears to be the
live session.

Cursor enrichment is strictly read-only. Token Meter opens `state.vscdb` with
SQLite `mode=ro` and `query_only`, retains WAL visibility for a live Cursor
process, uses a short busy timeout, and falls back to the per-session transcript
if the shared database is missing, locked, corrupt, or has an unsupported
schema. Request-trace parsing accepts only a bounded allowlist of completed span
names and derives timing fields; it does not expose raw request-log contents.
