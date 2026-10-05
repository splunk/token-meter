# Work insights: how it works

The current reference for the Work page logic. The
[design log](2026-09-30-work-insights-design.md) records why each piece
changed, iteration by iteration; this page describes what the code does now.
Constants named here live in `token_meter/domain/work.py` (aggregation and
suggestions), `token_meter/services/work_insights.py` (classifier), and
`token_meter/services/work_setup.py` (setup).

![How the Work page works](images/work-insights-flow.svg)

## 1. Setup (macOS only)

Work insights are off by default and supported on macOS only
(`work_insights_supported()`). Turning them on starts `WorkSetup` in the
background; it also runs at server start while enabled.

1. **Find Ollama.** If the configured URL (default `http://127.0.0.1:11434`)
   answers with Ollama 0.34 or newer, it is reused. If an Ollama is installed
   (`CLI_CANDIDATES`, including `~/Applications`) but not answering, setup
   waits two minutes, then stops with `ollama_offline` rather than replacing
   it. An Ollama older than 0.34 is bypassed in favor of the managed runtime.
2. **Otherwise install the pinned runtime.** Ollama 0.34.4 `ollama-darwin.tgz`
   from GitHub, checked against its pinned size and SHA-256, extracted with
   path checks (regular files with `O_EXCL|O_NOFOLLOW`, modes masked to 0755,
   same-folder symlinks only), and every Mach-O file must pass
   `codesign --verify --strict` with Ollama's Developer ID team `3MU9H2V9Y9`. It lives in
   `~/Library/Application Support/Token Meter/ollama/0.34.4` and runs as
   LaunchAgent `com.token-meter.ollama` on `127.0.0.1:11435` with logs sent to
   `/dev/null`. The settings URL then points there.
3. **Get the model.** If the model (`token-meter-jet`) is missing, 16 pinned
   Jet v6.2.0 files are downloaded from Hugging Face at a fixed commit, each
   checked by size and hash, with resume. Free space must cover the remaining
   download plus the import (about 20 GB at first). The model is imported with
   `ollama create -q int4`, then the download is deleted.

Loopback probes never use an HTTP proxy. Turning Work insights off cancels a
running setup at its next checkpoint and stops the managed Ollama; turning it
back on while the old run winds down restarts setup only if it is still on.
Status exposes a state, a reason code, byte counts, whether the Ollama is
managed, and the pinned version; no paths or error text. Uninstall removes
the managed Ollama, its model, and leftover setup downloads.

## 2. Classification

`session_summary` passes each session's typed human turns (and, for follow-ups,
the last 600 characters of the preceding assistant reply) to the classifier
through a bounded in-memory queue (256 items). Text is cleaned, compressed to
a skeleton (at most 2,000 characters), and sent only to the validated loopback
Ollama URL. Only salted session and turn keys, labels, confidences, prompt
versions, taxonomy hashes, and the model digest are stored.

| Question | Asked of | Answer | Unclear below |
| --- | --- | --- | --- |
| Work type | first substantive request (3+ words) | feature, debug (bug fixing), refactor, test, review (code review), plan, explore (questions and research), ops (DevOps and setup), docs, other (non-software) | 0.35 |
| Area | first substantive request | one of 2-8 editable areas; defaults follow the stack: Frontend & UI, Backend & APIs, Data & ML, Infrastructure & DevOps, Developer tooling & agents, Docs & writing, Non-code | 0.4 |
| Complexity | first substantive request | routine, everyday, complex, high-impact (probability-weighted level) | — |
| Pushback | every follow-up turn | yes/no: did the user say the previous work was wrong, broken, or not what they asked for? | 0.5 |

Choice questions are asked in both option orders and averaged to cancel
position bias. Labels carry a per-question prompt version
(`QUESTION_VERSIONS`: work type and area `p3`, complexity and pushback `p2`);
changing one relabels only that question. Area labels count whenever their
taxonomy hash matches, so older labels show until replaced. Cutoffs apply when
labels are read, so changing one needs no relabeling. An area answer between
0.25 and the 0.4 cutoff is kept as a **low-confidence guess** (`area_guess`):
it counts in that area, and the bar shows its spend lighter with a
"low-confidence" count. A request that fits two areas, such as work on a
developer tool with a UI, splits the model's confidence without being wrong;
in one 3-month check this moved Unclear from about $400 to $14. (The 0.4 area cutoff
labels 98% of a synthetic set at 81% accuracy, against 90% at 84% for 0.5.)

Sessions without a label fall in one of three groups, shown muted after the
areas: **No request text** (no typed request Token Meter can read, either
because the app does not record one in a readable form, as with Kiro and Pi,
or because the session never had one), **Outside labeling history** (last
active before the history setting), and **Not labeled yet** (waiting in the
queue).
Only the last is real backlog; labeling progress counts only labelable
sessions.

The worker paces requests (default 5 a minute; 5-60), pauses on battery, waits
when load exceeds 0.75 per CPU or the model slows 3x, and backs off when Ollama
is unreachable. History to backfill: 30, 90 (default), 365 days, or all.

## 3. From labels to outcomes

A session's **outcome** comes from its ordered pushback labels:

- **Single request**: one turn, or labeled with no classifiable follow-ups.
- **Accepted**: follow-ups, none of them pushback.
- **Recovered**: at least one pushback, but the last labeled follow-up was not.
- **Ended on pushback**: the last labeled follow-up was a pushback.
- **Unclear** (all low-confidence) and **Not labeled yet** (pending).

*Judged* sessions are accepted, recovered, or ended on pushback; *resolved*
sessions are accepted or recovered. **Cost per resolved session** is the spend
of resolved sessions divided by their count. Rates with fewer than 20 labeled
turns are marked "few".

**Model tiers** rank the models the user actually ran (each app's model counted
separately) by catalog output price into thirds: light, standard, premium.
Models without a catalog price get no tier. Complexity groups are
routine, everyday, and Complex+ (complex plus high-impact).

All modules count sessions *started* in the selected range (1 day, 1 week,
1 month, this month by day, 3, 6, 12 months, or all history), except the area allocation, which
spreads turns and spend across the days they happened (sessions without a
daily split count their cost on their start day).

## 4. Page modules

1. **Right-sizing** (first): suggestions (below), "Spend to review" (spend on
   complexity-labeled sessions in any mismatched cell, counted once), the
   biggest single possible saving, and spend split by model tier and by
   reasoning effort per complexity group. Both charts use the same three
   validated hues, cheap to expensive: teal (light models; low–medium effort),
   blue (standard; high), and amber (premium; xhigh, max, or ultra). Effort is
   folded into those three bands for readability; the tables keep every level.
   Striped segments are mismatches:
   premium models or xhigh/max/ultra effort on routine work, or light models on
   complex work whose pushback rate is above the median cell.
2. **Where the spend went**: spend by area with a sparkline per area (three or
   more buckets), and how sessions ended.
3. **Session tags** (computed, not from the model; a session can have several):

   | Tag | Rule |
   | --- | --- |
   | Marathon | active time at least 1 hour, and in the top 10% when 10 or more sessions have a duration |
   | Long thread | 30 or more requests |
   | Big spender | cost in the top 10%; needs 10 or more priced sessions |
   | Subagent team | 3 or more child runs (agent `parent_id` chains) |
   | Overkill | routine work on a premium model or xhigh/max/ultra effort |
   | Underpowered | complex work on a light model that got pushback |
   | Rescued / Ended on pushback / One-shot | the outcome above |

   "Top 10%" means strictly above the 90th-percentile value of the period.
4. **When you work**: session starts by local weekday and hour, and pushback
   rate by time of day (night, morning, afternoon, evening). The comparison
   line needs two bands with 20 or more labeled follow-ups.
5. **Pushback over time** (weekly; daily for short ranges, optionally per
   model) and **Cost per resolved session** by kind of work.
6. **Model choices**: top eight models by spend with pushback, resolved share,
   and cost per resolved session; the lowest is marked when at least two models
   have five or more judged sessions.

Bars, outcome rows, tags, table rows, and suggestions open the sessions behind
them (`/work/sessions`, the same filters and windowing as the aggregate); the
rhythm heatmap and time-of-day bands do not.

## 5. Suggestions

Right-sizing cells need at least 3 sessions, and the model suggestions have
the higher floors below. Suggestions are ranked by estimated saving, then
spend, and capped at 8. Savings are estimates and can overlap.

| Suggestion | When | Estimated saving |
| --- | --- | --- |
| **Try a cheaper model** for one kind of work at one complexity level | Compared with the most-used model in that cell (Unclear work types skipped). The alternative is cheaper per token, resolves within 5 points as often, gets no more than 5 points more pushback, and costs at most 70% per resolved session; the current model has 10+ judged sessions there, the alternative 5+; routine work is never pointed at a premium model. If the alternative has never been used on harder work, the suggestion says to keep the current model for it | (current − alternative cost per resolved) × current resolved sessions |
| **Use a newer version of the same model** | Same app and family, strictly newer version, cheaper per token, resolves within 5 points as often overall; 10+ judged sessions on the current model, 5+ on the newer | current spend × (1 − new price ÷ current price) |
| **Try a mid-priced model for routine work** | Routine sessions ran on premium models; names them and, per app, the most-used standard model there | spend × (1 − median standard price ÷ median premium price) |
| **Lower reasoning effort on routine work** | Routine sessions used xhigh, max, or ultra effort | none (no counterfactual cost) |
| **Use a stronger model for complex work** | Complex+ on light models with pushback above the median cell (20+ labeled turns) | none (usually costs more) |
| **Start a fresh session sooner** | Sessions with 30+ requests cost at least 1.5x more per request than sessions with 10 or fewer (5+ priced sessions each) | long-session spend − long requests × short-session cost per request (upper bound) |

Model families drop version numbers, vendor or region prefixes, `[1m]`-style
markers, `-v1:0`, and date stamps (`claude-opus-4-8` and `claude-opus-5-5` are
both `claude-opus`, versions `(4, 8)` and `(5, 5)`).

## 6. Live suggestions and notifications

Each running session on **Sessions → Current sessions** can show up to three
suggestions (`live_session_hints`), computed when the live state refreshes:

| Suggestion | When |
| --- | --- |
| Two pushbacks in a row | the last two confidently labeled follow-ups were pushback |
| Complex work on a light model | Complex+ request on a light-tier model that has had pushback in this session (a per-session check, looser than Right-sizing's median rule) |
| Routine request on a premium model | routine request on a premium-tier model; names the app's most-used standard model |
| High reasoning effort on routine work | routine request at xhigh, max, or ultra effort |
| Long session | 30 or more requests so far |

The first four need Work labels; the long-session check works without them.
Model-switch advice that needs outcome evidence (a cheaper or newer model)
stays in Right-sizing. A resumed session uses its newest trace file.

The menu bar gets the same suggestions as `live_hints` (stable id, title, and
body naming only the app and model) and sends one notification per suggestion
for each running session, remembering the last 200 ids. Notifications are
sent only while Work insights are on and **Notify me about live suggestions**
is checked (`live_notifications`, on by default within Work insights). The
first poll after installing only records what is already showing.

## 7. Limits

- Labels come from a 4B local model and are estimates; misclassified
  complexity or work type moves a session into the wrong comparison.
- Even within one complexity level, a cheaper model may have handled easier
  requests; a switch suggestion is something to try, not a guarantee.
- Suggestions shift while relabeling runs.
- Accuracy (2026-10-01, synthetic developer requests): work type 96%, area
  82%; see the design log for the method and caveats.
