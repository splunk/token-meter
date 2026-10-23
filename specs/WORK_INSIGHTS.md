# Work insights: how it works

The current reference for the Work page logic. The
[design log](2026-09-30-work-insights-design.md) records why each piece
changed, iteration by iteration; this page describes what the code does now.
Constants named here live in `token_meter/domain/work.py` (aggregation and
suggestions), `token_meter/domain/work_evidence.py` (per-request cost and
action evidence), `token_meter/services/work_insights.py` (classifier), and
`token_meter/services/work_setup.py` (setup).

## 1. Setup (macOS only)

Work insights are off by default and supported on macOS only
(`work_insights_supported()`). Turning them on starts `WorkSetup` in the
background; it also runs at server start while enabled.

1. **Find Ollama.** If the configured URL (default `http://127.0.0.1:11434`)
   answers with Ollama 0.34 or newer, it is reused. If an Ollama is installed
   (`CLI_CANDIDATES`, including `~/Applications`) but not answering, setup
   opens the Ollama app (when it is the app) and waits two minutes; if it
   still does not answer at the default address, setup uses the managed
   runtime instead of failing on every retry. An address the user set is
   never replaced: setup stops with `ollama_offline` instead. An Ollama older than 0.34 is bypassed in favor of the
   managed runtime.
2. **Otherwise install the pinned runtime.** Ollama 0.34.4 `ollama-darwin.tgz`
   from GitHub, checked against its pinned size and SHA-256, extracted with
   path checks (regular files with `O_EXCL|O_NOFOLLOW`, modes masked to 0755,
   same-folder symlinks only), and every Mach-O file must pass
   `codesign --verify --strict` with Ollama's Developer ID team `3MU9H2V9Y9`. It lives in
   `~/Library/Application Support/Token Meter/ollama/0.34.4` and runs as
   LaunchAgent `com.token-meter.ollama` on `127.0.0.1:11435` with logs sent to
   `/dev/null`. Its first start can take a while because macOS checks the
   fresh ~500 MB bundle (17 s measured on an M4 Pro, 0.3 s on later starts),
   so setup waits up to two minutes (`MANAGED_START_WAIT_S`). The settings
   URL then points there. When setup moves the URL or finishes, the worker
   retries at once rather than waiting out a backoff from before Ollama was
   ready (`connection_changed`). A request to a model that is not in memory
   (the first one, or one after Ollama's two-minute keep-alive) may wait up
   to `COLD_LOAD_TIMEOUT_S` (5 minutes): it loads about 5 GB from disk, and a
   fresh Ollama also prepares its GPU code then, so a short timeout would
   cancel the load on every retry. Later calls keep the short timeout, and a
   timeout shows as "Ollama is slow to answer" rather than "Can't reach
   Ollama". While a cold call loads, a pause or turning Work insights off
   takes effect when it returns (at most five minutes), and the memory guard
   checks again only before the next call. Cold calls are left out of the
   slow-model baseline.
3. **Get the model.** If the model (`token-meter-gemma`) is missing, Google's
   official Gemma 4 E4B quantization-aware 4-bit GGUF
   ([`gemma-4-E4B_q4_0-it.gguf`](https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf),
   Apache-2.0, about 5 GB; the vision projector is not needed) is downloaded
   from Hugging Face at commit `4b4a2c1d`, checked by size and SHA-256, with
   resume. Free space must cover the remaining download plus Ollama's copy of
   it (about 11 GB at first). The GGUF is imported as is with `ollama create`,
   then the download is deleted. In Token Meter's own Ollama, the retired
   `token-meter-jet` and `token-meter-winnow` models are removed afterwards; a
   user's own Ollama keeps every model it has. Saved settings that still name
   either retired model read as `token-meter-gemma`, but the new download does
   not start on its own: the Work page explains the new model and waits for
   **Retry setup**. Turning Work insights on or choosing a different model
   also counts as agreement; a pause, pace, history, area, or notification change keeps
   the retired name so setup keeps asking. Saving `token-meter-jet` is
   refused. The Modelfile carries the license, so it stays with the model
   after the download is deleted; uninstall also removes partial
   `gemma-4-e4b-qat-*`, `winnow-e4b-*`, and `jet-v*` downloads.

Loopback probes never use an HTTP proxy. Turning Work insights off cancels a
running setup at its next checkpoint and stops the managed Ollama; turning it
back on while the old run winds down restarts setup only if it is still on.
Downloads are built for weak connections. Each file (the Ollama archive and
the model) is tried three times, resuming where it stopped, and then once
with `/usr/bin/curl`, which some networks treat differently from Python;
every copy must still match the pinned size and SHA-256. A file that arrives
short is kept so the next try resumes; one that is the wrong size or hash is
deleted. A network, download-check, or Ollama-start failure is then retried on
its own after 1, 5, 15, 30, and then every 60 minutes while Work insights are
on; **Retry setup** skips the wait, and turning Work insights off ends it.

Status exposes a state, a reason code, a detail code that says which check
failed (`size`, `hash`, `archive`, `signature`, `http_status`, `tls`,
`timeout`, `connection`, `curl`, `disk`, `start_timeout`, `import_failed`),
byte counts, the next automatic retry and the attempt count, whether the
Ollama is managed, and the pinned version; no paths or error text. The Work
page turns these into steps (Ollama, model download, prepare, label), a
progress bar, the download speed and time left, a note on slow connections,
and a plain reason with the next retry time. Setup writes each state and
failure (reason and detail code) to the server's error log
(`meter.err.log`). When the labeling worker hits an unexpected error, status
and the log name the exception type and the innermost Token Meter
`file:line`, never its message. Uninstall removes the managed Ollama, its
model, and leftover setup downloads.

## 2. Requests: cost and what happened

Work is measured per **request**: one typed request and everything the agent
did until the next one. When a session is parsed (and Work insights are on),
each runtime adapter hands `attach_work_requests` its typed turns, its priced
assistant events (time, cost, model, effort), and, for Claude and Codex, its
tool calls (`claude_work_actions`, `codex_work_actions`, including tools
called from Codex code-mode scripts).

- **Cost split** (`request_slices`): an event belongs to the latest request
  that started at or before it (resumed traces can list requests out of
  order, so ownership goes by time). Cost before the first request goes to the
  first. A request's model and effort are the ones that cost the most in its
  slice. Cursor uses each execution's cost; OpenCode each message's.
  Without timestamps, each day's cost is spread over that day's requests.
- **Evidence** (`evidence_text`): one line naming the files the agent changed
  for that request by basename ("Files the agent changed: page.html, app.py
  and 3 more."), or that it changed none or only committed or pushed. It goes
  to the classifier prompt in memory and is never stored. The row keeps only
  content-free slices: cost, model, effort, counts of tool kinds and command
  kinds (test, git, build, deploy, ...), and file categories (frontend,
  backend, docs, ...). Cursor and OpenCode get cost slices but no file names
  (their summaries keep only tool names, or no per-message tool timing).

## 3. Classification

The classifier queue (bounded, 256 items) receives each request's text,
the earlier substantive request in the session (first 400 characters), the
last 600 characters of the preceding assistant reply, and the evidence line.
Text is cleaned (runtime wrappers such as in-app browser context are dropped,
uploaded files become `[uploaded: name]`, interrupt markers are ignored),
compressed to a skeleton (at most 2,000 characters), and sent only to the
validated loopback Ollama URL. Only salted session and turn keys, labels,
confidences, prompt versions, taxonomy hashes, and the model digest are stored.

| Question | Asked of | Answer | Unclear below |
| --- | --- | --- | --- |
| Work type | every substantive request (3+ words) | feature, debug (bug fixing), refactor, test, review (code review), plan, explore (questions and research), ops (DevOps and setup), docs, other (non-software) | 0.5 |
| Area | every substantive request | one of 2-8 editable areas; defaults follow the stack: Frontend & UI, Backend & APIs, Data & ML, Infrastructure & DevOps, Developer tooling & agents, Docs & writing, Non-code | 0.4 |
| Complexity | every substantive request | routine, everyday, complex, high-impact (probability-weighted level against cut-offs 0.75 / 1.5 / 2.5) | — |
| Pushback | every follow-up turn | yes/no from four checks: the previous work was wrong; the user is unhappy with the last result, even mildly; the agent's change is not working or not visible; the user doubts or disagrees with the assistant | — |

A short reply ("yes", "go on") is not labeled for work type, area, or
complexity; it keeps the labels of the request before it (requests before
the first label take the first label). Live sessions go first, then requests
that have never been labeled, then relabels (a new prompt version or areas);
the backlog refills sessions with no labels first. Within a session the queue
labels the most expensive requests first.

Every question uses Gemma's raw turn format: the state and the question go in
as JSON strings with lettered options, sent raw to `/api/generate`, and the
answer is read from the first token's top-20 log-probabilities (a letter
missing from them sits 3 below the lowest returned one; softmax temperature 1).
Area is asked first, in both option orders, and the two answers are averaged.
With the default areas the average is divided by `AREA_PRIOR` (the model's
average answer on a labeled development set) to the power `AREA_PRIOR_ALPHA`,
because Gemma leans towards some areas whatever the request; custom areas get
no correction. The first-order answer is added to the work-type state as
`Area of this request: …`. Complexity flattens the level distribution at
temperature 2 and reads the probability-weighted level against the cut-offs
0.95, 1.35, and 2.5. Pushback sees the earlier request, the files the agent
changed in its previous turn, the reply tail, and the latest message. Each of
the four checks is asked after six invented worked examples, given as earlier
turns (`PUSHBACK_SHOTS`; none comes from real sessions); their
yes-probabilities are combined by a logistic fit (`PUSHBACK_WEIGHTS`, decision
at `PUSHBACK_THRESHOLD`), and the stored confidence is centred on that
decision. The priors, temperatures, cut-offs, and weights were fitted on the
development set only and checked on separate test and held-out sets.
Labels carry a per-question prompt version (`QUESTION_VERSIONS`, all `g1`);
changing one relabels only that question, and older labels keep showing
until replaced. Area labels count whenever their taxonomy hash
matches the current areas, or an earlier default set with the same names
(reworded descriptions), until they are relabeled. Cutoffs apply when labels are read, so changing one needs no
relabeling. An area answer between 0.25 and the 0.4 cutoff is kept as a
**low-confidence guess** (`area_guess`): it counts in that area, and the bar
shows that spend lighter.

Sessions without a label fall in one of three groups, shown muted after the
areas: **No request text** (no typed request Token Meter can read, either
because the app does not record one in a readable form, as with Kiro and Pi,
or because the session never had one), **Outside labeling history** (last
active before the history setting), and **Not labeled yet** (waiting in the
queue). Only the last is real backlog; labeling progress counts only
labelable sessions.

The worker paces model calls. The default, **Automatic**, allows 60 a minute
while the last busy check found 35% or less of CPU time busy and the Mac on
power, and 5 a minute otherwise (or when the reading is unknown); GPU work does
not count as busy; a fixed pace of 5-60 is a cap. A 5
saved before the pace version was the old default and becomes Automatic. It
pauses on battery,
waits when more than 75% of all CPU time is busy (measured from the CPU tick
counters since the last check; the load average counts waiting threads and
reads high on an idle Mac with many apps open, so it is only a fallback where
there is no counter) or the model slows 3x, and backs off when Ollama is
unreachable. In a live session the newest request's kind of work, area, and
complexity wait until the next request starts or the session is quiet for
ten minutes, so the label sees what the request led to; its pushback check
runs at once. A memory guard, checked before every model call, keeps
labeling from adding to memory pressure: the model is loaded only when free
memory covers it (6 GB) plus a headroom of 2 GB or a tenth of the Mac's
memory, whichever is larger, and a loaded model is unloaded, and labeling
waits ("memory is low"), when macOS reports critical memory pressure or free
memory falls below the headroom. A model quiet for longer than Ollama's
two-minute keep-alive counts as unloaded, and a model left resident by an
earlier run is unloaded when Ollama lists it. A Mac with less memory than
the model plus twice the headroom never loads it ("not enough memory on this
Mac"), and setup does not download it there. Free memory and pressure come from `kern.memorystatus_level` and
`kern.memorystatus_vm_pressure_level`. Each scheduled recheck that is still
short doubles the wait, from one minute up to 15, and an early wake keeps
the schedule, so loading and unloading cannot thrash; labeling resumes on
its own once memory is free. The guard cannot stop other apps from using up
memory; it only keeps the model out of the way. A substantive follow-up takes eight calls (two for area,
one each for work type and complexity, and four pushback checks), so relabeling a
long history at 5 a minute takes hours; Automatic runs faster while the Mac is quiet.
The model stays loaded while work is queued or backlog is ready to load, and
is unloaded from Ollama as soon as nothing is (and on pause or throttling),
so its memory is free whenever nothing is waiting.
History to backfill: 30, 90 (default), 365 days, or all.

## 4. From labels to outcomes

Three units, each used where it fits:

- **Requests** carry spend. Where the spend went, right-sizing, reasoning
  effort, and pushback over time add up requests. A request's *rework* is
  whether the request after it was a pushback.
- **Tasks** carry outcomes: a run of consecutive requests with the same kind
  of work. A task is judged from the pushback on its follow-ups and on the
  request that ended it: **accepted** (none), **recovered** (pushback, then
  not), **ended on pushback** (the last one was), or unjudged (no labeled
  follow-up). *Resolved* tasks are accepted or recovered. **Cost per resolved
  task** is the spend of resolved tasks divided by their count; model fit,
  the scorecard, and model-switch suggestions use tasks too.
- **Sessions** keep start time, duration, child runs, tags, and how they
  ended: **Single request**, **Accepted**, **Recovered**, **Ended on
  pushback**, **Unclear** (all low-confidence), or **Not labeled yet**. A
  session's headline area, work type, and complexity are the ones that cost
  the most across its requests.

Rates with fewer than 20 labeled follow-ups are marked "few".

**Model tiers** rank the models the user actually ran (each app's model counted
separately) by catalog output price into thirds: light, standard, premium.
Models without a catalog price get no tier. Complexity groups are
routine, everyday, and Complex+ (complex plus high-impact).

Requests and tasks count in the selected range (1 day, 1 week, 1 month, Month
(from the 1st, by day), 3, 6, 12 months, or all history) by the day they
happened; session modules (tags, how sessions ended, when you work) count
sessions *started* in the range.

## 5. Page modules

1. **Right-sizing** (first): three cards. **Model suggestions** shows the
   biggest single possible saving and the top three model suggestions by
   saving (newer version, cheaper model for a kind of work, premium on routine,
   light on complex). **Reasoning suggestions** shows routine requests on high
   effort and routine spend by effort. **Pushback** shows the share of labeled
   follow-ups that pushed back, a weekly trend (weeks with 10+ follow-ups), and
   the models with the least and most pushback (20+ follow-ups each). Under
   **View as tables**: every suggestion, spend split by model tier and by
   reasoning effort per complexity group, and the grid. Both charts use the same three
   validated hues, cheap to expensive: teal (light models; low–medium effort),
   blue (standard; high), and amber (premium; xhigh, max, or ultra). Effort is
   folded into those bands for readability; the tables keep every level. A
   Claude or Codex request with no recorded effort ran at the app's default, so
   it gets a fourth band, **Default** (gray, "not recorded"): shown, never
   flagged, never compared. Other apps never record effort and are left out.
   Striped segments are mismatches:
   premium models or xhigh/max/ultra effort on routine work, or light models on
   complex work whose pushback rate is above the median cell.
2. **Where the spend went**: spend by area, split by request, with a
   sparkline per area (three or more buckets), and how sessions ended.
3. **Session tags** (computed, not from the model; a session can have several):

   | Tag | Rule |
   | --- | --- |
   | Marathon | active time at least 1 hour, and in the top 10% when 10 or more sessions have a duration |
   | Long thread | 30 or more requests |
   | Big spender | cost in the top 10%; needs 10 or more priced sessions |
   | Subagent team | 3 or more child runs (agent `parent_id` chains) |
   | Overkill | more than half the session's spend is routine requests on a premium model or xhigh/max/ultra effort |
   | Underpowered | a complex task on a light model that got pushback |
   | Rescued / Ended on pushback / One-shot | the outcome above |

   "Top 10%" means strictly above the 90th-percentile value of the period.
4. **When you work**: session starts by local weekday and hour, and pushback
   rate by time of day (night, morning, afternoon, evening). The comparison
   line needs two bands with 20 or more labeled follow-ups.
5. **Pushback over time** (weekly; daily for short ranges, optionally per
   model) and **Cost per resolved task** by kind of work.
6. **Model choices**: top eight models by spend with pushback, resolved share,
   and cost per resolved task; the lowest is marked when at least two models
   have five or more judged tasks.

Bars, outcome rows, tags, table rows, and suggestions open the sessions behind
them (`/work/sessions`, the same filters and windowing as the aggregate). A
request filter (area, kind of work, complexity, tier, effort, model) lists
sessions with any matching request, ranked by the matching spend. The rhythm
heatmap and time-of-day bands do not open sessions.

## 6. Suggestions

Right-sizing cells need at least 3 requests, and the model suggestions have
the higher floors below (in tasks). Suggestions are ranked by estimated saving, then
spend, and capped at 8. Savings are estimates and can overlap.

| Suggestion | When | Estimated saving |
| --- | --- | --- |
| **Try a cheaper model** for one kind of work at one complexity level | Compared with the most-used model in that cell (Unclear work types skipped). The alternative is cheaper per token, resolves within 5 points as often, gets no more than 5 points more pushback, and costs at most 70% per resolved task; the current model has 10+ judged tasks there, the alternative 5+; routine work is never pointed at a premium model. If the alternative has never been used on harder work, the suggestion says to keep the current model for it | (current − alternative cost per resolved task) × current resolved tasks |
| **Use a newer version of the same model** | Same app and family, strictly newer version, cheaper per token, resolves within 5 points as often overall; 10+ judged tasks on the current model, 5+ on the newer | current spend × (1 − new price ÷ current price) |
| **Try a mid-priced model for routine work** | Routine requests ran on premium models; names them and, per app, the most-used standard model there | spend × (1 − median standard price ÷ median premium price) |
| **Lower reasoning effort on routine work** | Routine requests used xhigh, max, or ultra effort | none (no counterfactual cost) |
| **Use a stronger model for complex work** | Complex+ on light models with pushback above the median cell (20+ labeled turns) | none (usually costs more) |
| **Start a fresh session sooner** | Sessions with 30+ requests cost at least 1.5x more per request than sessions with 10 or fewer (5+ priced sessions each) | long-session spend − long requests × short-session cost per request (upper bound) |

### Effort by model

Under the Right-sizing cards, "Pushback by reasoning effort" shows one model
at a time, chosen from a dropdown (scoped by app, top five by spend among
models with labeled requests; the panel is hidden when no model has one), as a
complexity × reasoning-effort matrix. Each cell holds one number, the pushback
rate, shaded by rate, with follow-ups, requests, and cost in its tooltip; each
effort column shows its cost per request once. Effort means something different on each model, so
columns are that model's own levels and levels are never compared across
models. A cell with fewer than 20 labeled follow-ups shows a faint dot.
Only requests with a complexity label count (Codex `none` is a level). A
Claude or Codex request with no recorded effort goes in a **default** column:
it ran at the app's default level, which the trace does not name, so it is
never compared with another level. Requests from apps that never record effort
(Cursor, OpenCode, Kiro, and others), and effort values that are not a known
level, are named as left out. A cell whose requests are all unpriced shows no cost rather than $0.
Codex records effort per request; Claude Code records `perTurnEffort` (older
builds: `effort`), which is empty when the default is in use.

Verdicts compare levels next to each other among the model's own levels,
within one complexity row, when both have at least 20 labeled follow-ups and a
known cost (a thin level in between means no call across it). Because many
cells are compared at once, both tests use 99% rather than 95%:

| Verdict | When | Shown saving |
| --- | --- | --- |
| **Safe to lower** | The lower level is cheaper per request and its pushback is no worse than the next level's by more than 5 points: the upper end of the 99% Agresti-Caffo interval of the difference (one pushback and one not added to each side, so no pushback on either side is not proof) stays below 5 points | (cost per request gap) × requests at the higher level, over the period |
| **Worth raising** | The lower level's pushback is higher by a two-proportion z above 2.576 | none |
| **No call yet** | Otherwise | none |

Model families drop version numbers, vendor or region prefixes, `[1m]`-style
markers, `-v1:0`, and date stamps (`claude-opus-4-8` and `claude-opus-5-5` are
both `claude-opus`, versions `(4, 8)` and `(5, 5)`).

## 7. Live suggestions and notifications

Each running session on **Sessions → Current sessions** can show up to three
suggestions (`live_session_hints`), computed when the live state refreshes.
The routine checks weigh the whole session, because a short follow-up judged
on its own text reads routine even when it starts heavy work, and they advise
the next session, because switching mid-session re-reads the conversation:

| Suggestion | When |
| --- | --- |
| Two pushbacks in a row | the last two confidently labeled follow-ups were pushback |
| Complex work on a light model | the latest labeled request is Complex+ on a light-tier model that has had pushback in this session (a per-session check, looser than Right-sizing's median rule) |
| Mostly routine work on a premium model | routine requests on a premium-tier model are more than half of the spend of requests with their own label; names the app's most-used standard model |
| Mostly routine work at high effort | routine requests at xhigh, max, or ultra effort are more than half of the spend of requests with their own label |
| Long session | 30 or more requests so far |

Here, unlike the Work page, a request does not inherit the label before it:
a request not labeled yet would otherwise take a short question's "routine"
and turn a heavy session into routine work. The first four need Work labels;
the long-session check works without them.
Model-switch advice that needs outcome evidence (a cheaper or newer model)
stays in Right-sizing. A resumed session uses its newest trace file.

The menu bar gets the same suggestions as `live_hints` (stable id, title, and
body naming only the app and model) and sends one notification per suggestion
for each running session, remembering the last 200 ids. Notifications are
sent only while Work insights are on and **Notify me about live suggestions**
is checked (`live_notifications`, on by default within Work insights). The
first poll after installing only records what is already showing.

## 8. Limits

- Labels come from a 4B local model and are estimates; misclassified
  complexity or work type moves a session into the wrong comparison.
- Even within one complexity level, a cheaper model may have handled easier
  requests; a switch suggestion is something to try, not a guarantee.
- Suggestions shift while relabeling runs.
- Accuracy on 120 real local requests (2026-10-05, blind gold labels, see the
  design log): work type 79%, area 85% (79% and 92% weighted by cost),
  against 50% and 61% when every request took its session's first label.
  Complexity was not measured. The set is small and the format was chosen on
  it, so treat these as rough.
