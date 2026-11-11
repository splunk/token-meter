"""Sessions › By branch, the shared branch chip, and Git › Branches on the dashboard."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.dashboard_page import DASHBOARD_PAGE


HARNESS = r"""
const fs=require('fs');const page=fs.readFileSync(process.argv[1],'utf8');
function extract(name){let start=page.indexOf(`function ${name}(`);if(start<0)throw new Error('missing '+name);let i=page.indexOf('){',start)+1,depth=0;for(;i<page.length;i++){if(page[i]==='{')depth++;else if(page[i]==='}'&&--depth===0)return page.slice(start,i+1);}throw new Error('unclosed '+name);}
function constLine(name){const m=page.match(new RegExp('^const '+name+'=.*$','m'));if(!m)throw new Error('missing const '+name);return m[0].replace(/^const /,'var ');}
function load(names){names.forEach(name=>{(0,eval)(extract(name).replace(/^function ([\w$]+)/,'globalThis.$1=function $1'));});}
function loadConst(names){names.forEach(name=>(0,eval)(constLine(name)));}
"""

BRANCH_HELPERS = ["isTrunkBranch", "branchIssueKey", "isAutoWorktreeBranch", "branchReadableName"]
BRANCH_CONSTS = ["BRANCH_TRUNK_NAMES", "BRANCH_PREFIX_PATTERN", "BRANCH_ISSUE_PATTERN", "BRANCH_CHIP_ICON", "esc", "money", "f",
                 "countWord"]


@unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
class BranchScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(DASHBOARD_PAGE).read_text(encoding="utf-8")

    def run_node(self, body):
        result = subprocess.run(["node", "-e", HARNESS + body, str(DASHBOARD_PAGE)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_readable_names_issue_keys_and_trunk(self):
        out = self.run_node(f"""
loadConst({json.dumps(BRANCH_CONSTS)});load({json.dumps(BRANCH_HELPERS)});
const names=['feat/branch-spend','fix/login-redirect','codex/fix-retry-backoff','claude/quiet-lake-9a5eb7',
 'claude/tax-report-export-tool-8ebc6d','FIG-7931-goals-development-phase-1','main','feature/ab-1','docs/setup_guide','',null];
process.stdout.write(JSON.stringify({{
 readable:names.map(branchReadableName),
 keys:names.map(branchIssueKey),
 trunk:['main','master','develop','dev','trunk','feat/main','mainline'].map(isTrunkBranch),
 auto:['claude/quiet-lake-9a5eb7','claude/tax-report-export-tool-8ebc6d','codex/quiet-lake-9a5eb7'].map(isAutoWorktreeBranch),
}}));
""")
        self.assertEqual(out["readable"], [
            "Branch spend", "Login redirect", "Fix retry backoff", "Unnamed worktree",
            "Tax report export tool", "Goals development phase 1", "Main", "Ab 1", "Setup guide",
            "No branch recorded", "No branch recorded",
        ])
        self.assertEqual(out["keys"], ["", "", "", "", "", "FIG-7931", "", "", "", "", ""])
        self.assertEqual(out["trunk"], [True, True, True, True, True, False, False])
        self.assertEqual(out["auto"], [True, False, False])

    def test_issue_key_needs_two_digits_and_uppercase(self):
        out = self.run_node(f"""
loadConst({json.dumps(BRANCH_CONSTS)});load({json.dumps(BRANCH_HELPERS)});
process.stdout.write(JSON.stringify(['feat/ABC-12-x','feat/ABC-1-x','codex/issue-26-agent-tooling','FIG-123','x/AB-99','xAB-99'].map(branchIssueKey)));
""")
        self.assertEqual(out, ["ABC-12", "", "", "FIG-123", "AB-99", ""])

    def test_branch_chip_helper_output_and_link_target(self):
        out = self.run_node(f"""
loadConst({json.dumps(BRANCH_CONSTS)});load({json.dumps(BRANCH_HELPERS + ["branchChipHtml"])});
process.stdout.write(JSON.stringify({{
 link:branchChipHtml('FIG-7931-goals<x>',{{project:'~/repo'}}),
 plain:branchChipHtml('feat/a',{{interactive:false}}),
 noKey:branchChipHtml('FIG-7931-goals',{{interactive:false,issue:false}}),
 empty:branchChipHtml(''),
}}));
""")
        link = out["link"]
        self.assertTrue(link.startswith("<button class=branchChip type=button"), link)
        self.assertIn('data-branch-chip="FIG-7931-goals&lt;x&gt;"', link)
        self.assertIn('data-branch-project="~/repo"', link)
        self.assertIn("aria-label=\"Branch FIG-7931-goals&lt;x&gt;. Show in Sessions, By branch\"", link)
        self.assertIn("<span class=branchChipName>FIG-7931-goals&lt;x&gt;</span>", link)
        self.assertIn("<span class=branchChipKey>FIG-7931</span>", link)
        self.assertIn("<svg class=branchChipIcon", link)
        self.assertNotIn("<x>", link)
        self.assertTrue(out["plain"].startswith("<span class=branchChip"), out["plain"])
        self.assertNotIn("data-branch-chip", out["plain"])
        self.assertNotIn("branchChipKey", out["noKey"])
        self.assertEqual(out["empty"], "")

    def branch_view(self, call):
        return self.run_node(f"""
loadConst({json.dumps(BRANCH_CONSTS + ["OTHER_LOCAL_SESSIONS_PROJECT", "runtimeId", "appFilterGroup"])});
load({json.dumps(BRANCH_HELPERS + ["projectFilterValue", "branchProjectName", "branchGitMatch", "branchMatchesQuery", "branchSpendView"])});
const day=86400,now=1_800_000_000;
const rows=[
 {{id:'a1',session:'a1.jsonl',provider:'claude',project:'~/repo',branch:'feat/branch-spend',cost:5,mtime:now-100,wall_duration_s:60,title:'A one',availability:{{cost:true}}}},
 {{id:'a2',session:'a2.jsonl',provider:'codex',project:'~/repo',branch:'feat/branch-spend',cost:3,mtime:now-2*day,wall_duration_s:60,title:'A two',availability:{{cost:true}}}},
 {{id:'b1',session:'b1.jsonl',provider:'claude',project:'~/repo',branch:'FIG-7931-goals',cost:9,mtime:now-3*day,title:'B',availability:{{cost:true}}}},
 {{id:'c1',session:'c1.jsonl',provider:'claude',project:'~/repo',branch:'fix/old',cost:1,mtime:now-40*day,title:'C old',availability:{{cost:true}}}},
 {{id:'m1',session:'m1.jsonl',provider:'claude',project:'~/repo',branch:'main',cost:20,mtime:now-50,title:'Main work',availability:{{cost:true}}}},
 {{id:'n1',session:'n1.jsonl',provider:'cursor',project:'~/repo',cost:4,mtime:now-60,title:'Cursor',availability:{{cost:true}}}},
 {{id:'u1',session:'u1.jsonl',provider:'claude',project:'~/repo',branch:'feat/unpriced',cost:0,mtime:now-70,title:'Unpriced',availability:{{cost:false}}}},
 {{id:'k1',session:'k1.jsonl',provider:'claude',project:'~/repo',branch:'feat/branch-spend',cost:2,mtime:now-80,title:'Child',is_child_session:true,root_session_id:'a1',availability:{{cost:true}}}},
];
const git=[
 {{branch:'feat/branch-spend',project:'repo · abc123',status:'pushed',pushes:2,commits:3,added:40,deleted:2}},
 {{branch:'FIG-7931-goals',project:'repo · abc123',status:'in_progress',pushes:0,commits:0,added:0,deleted:0}},
 {{branch:'fix/old',project:'other · 999999',status:'not_pushed'}},
 {{branch:'fix/old',project:'repo2 · 888888',status:'pushed'}},
];
const base={{rangeStart:now-30*day,rangeEnd:0,gitRows:git}};
const view=opts=>branchSpendView(rows,{{...base,...opts}});
process.stdout.write(JSON.stringify({call}));
""")

    def test_branch_view_groups_totals_and_status(self):
        out = self.branch_view("""(()=>{const v=view({});return {
 names:v.rows.map(r=>r.branch),spend:v.rows.map(r=>r.spend),sessions:v.rows.map(r=>r.sessions),status:v.rows.map(r=>r.status),
 lines:v.rows.map(r=>r.git?[r.git.added,r.git.deleted]:null),apps:v.rows[0].apps,totals:v.totals,total:v.total,
 share:Math.round(v.rows[0].share*1000)/1000,sessionIds:v.rows[0].sessionRows.map(r=>r.id),keys:v.rows.map(r=>r.key)};})()""")
        self.assertEqual(out["names"], ["feat/branch-spend", "FIG-7931-goals", "feat/unpriced"])
        self.assertEqual(out["spend"], [10, 9, None], "child runs add spend to their branch")
        self.assertEqual(out["sessions"], [2, 1, 1], "child runs are not counted as sessions")
        self.assertEqual(out["status"], ["pushed", "in_progress", "no_git_evidence"])
        self.assertEqual(out["lines"], [[40, 2], [0, 0], None])
        self.assertEqual(out["apps"], ["claude", "codex"])
        self.assertEqual(out["sessionIds"], ["a1", "a2"])
        self.assertEqual(out["keys"][0], "feat/branch-spend:~/repo")
        self.assertFalse(any(re.search(r"[\x00-\x1f]", key) for key in out["keys"]), "row keys must survive HTML attributes")
        self.assertEqual(out["totals"]["feature"], 19)
        self.assertEqual(out["totals"]["trunk"], 20)
        self.assertEqual(out["totals"]["noBranch"], 4)
        self.assertEqual(out["totals"]["top"]["branch"], "feat/branch-spend")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["share"], round(10 / 19, 3), "share is part of the feature-branch total")

    def test_branch_view_filters_search_status_main_and_time(self):
        out = self.branch_view("""({
 issue:view({query:'fig-7931'}).rows.map(r=>r.branch),
 readable:view({query:'branch spend'}).rows.map(r=>r.branch),
 raw:view({query:'feat/'}).rows.map(r=>r.branch),
 pushed:view({status:'pushed'}).rows.map(r=>r.branch),
 noGit:view({status:'no_git_evidence'}).rows.map(r=>r.branch),
 inProgress:view({status:'in_progress'}).rows.map(r=>r.branch),
 statusTotals:view({status:'pushed'}).totals.feature,
 hiddenMain:view({}).rows.some(r=>r.trunk),
 shownMain:view({showMain:true}).rows.filter(r=>r.trunk).map(r=>[r.branch,r.status]),
 all:view({rangeStart:0}).rows.map(r=>r.branch).sort(),
 ambiguous:view({rangeStart:0}).rows.find(r=>r.branch==='fix/old').status,
 app:view({app:'codex'}).rows.map(r=>[r.branch,r.spend]),
 project:view({project:'~/elsewhere'}).rows.length,
})""")
        self.assertEqual(out["issue"], ["FIG-7931-goals"])
        self.assertEqual(out["readable"], ["feat/branch-spend"])
        self.assertEqual(out["raw"], ["feat/branch-spend", "feat/unpriced"])
        self.assertEqual(out["pushed"], ["feat/branch-spend"])
        self.assertEqual(out["noGit"], ["feat/unpriced"])
        self.assertEqual(out["inProgress"], ["FIG-7931-goals"])
        self.assertEqual(out["statusTotals"], 19, "totals ignore the Status filter")
        self.assertFalse(out["hiddenMain"])
        self.assertEqual(out["shownMain"], [["main", "trunk"]])
        self.assertEqual(out["all"], ["FIG-7931-goals", "feat/branch-spend", "feat/unpriced", "fix/old"])
        self.assertEqual(out["ambiguous"], "no_git_evidence")
        self.assertEqual(out["app"], [["feat/branch-spend", 3]])
        self.assertEqual(out["project"], 0)

    def test_rows_cover_the_whole_branch_and_totals_match_rows(self):
        out = self.branch_view("""(()=>{
 const extra=[...rows,
  {id:'o1',session:'o1.jsonl',provider:'claude',project:'~/repo',branch:'feat/branch-spend',cost:7,mtime:now-60*day,last_activity_ts:now-60*day,title:'Old',availability:{cost:true}},
  {id:'r1',session:'r1.jsonl',provider:'claude',project:'~/repo',branch:'fix/resumed',cost:6,mtime:now-10,last_activity_ts:now-45*day,title:'Touched later',availability:{cost:true}},
  {id:'w1',session:'w1.jsonl',provider:'claude',project:'~/repo-wt',repo:'repo · abc123',branch:'feat/wt',cost:2,mtime:now-100,title:'Worktree',availability:{cost:true}},
  {id:'w2',session:'w2.jsonl',provider:'codex',project:'~/repo',repo:'repo · abc123',branch:'feat/wt',cost:1,mtime:now-200,title:'Main checkout',availability:{cost:true}},
 ];
 const git=[...base.gitRows,{branch:'feat/wt',project:'repo · abc123',status:'pushed',pushes:1,commits:1,added:3,deleted:0},{branch:'feat/wt',project:'repo2 · 888888',status:'not_pushed',pushes:0}];
 const v=branchSpendView(extra,{...base,gitRows:git});
 const row=name=>v.rows.filter(r=>r.branch===name);
 const spend=row('feat/branch-spend')[0];
 return {
  spend:spend.spend,listed:spend.sessionRows.map(r=>r.id),listedSum:spend.sessionRows.reduce((a,r)=>a+r.cost,0)+2,
  resumed:row('fix/resumed').length,
  wt:row('feat/wt').map(r=>[r.spend,r.sessions,r.status,r.projectKey]),
  featureTotal:v.totals.feature,rowSum:v.rows.filter(r=>!r.trunk).reduce((a,r)=>a+(r.spend||0),0),
  shareSum:Math.round(v.rows.reduce((a,r)=>a+(r.share||0),0)*1e6)/1e6,
  topShare:v.totals.top.share===v.totals.top.spend/v.totals.feature,
 };})()""")
        self.assertEqual(out["spend"], 17, "an older session still belongs to its branch")
        self.assertEqual(out["listed"], ["a1", "a2", "o1"])
        self.assertEqual(out["listedSum"], out["spend"], "listed sessions plus the child run sum to Spend")
        self.assertEqual(out["resumed"], 0, "range uses last activity, not file modification time")
        self.assertEqual(out["wt"], [[3, 2, "pushed", "repo · abc123"]], "worktrees of one repository are one row")
        self.assertEqual(out["featureTotal"], out["rowSum"])
        self.assertEqual(out["shareSum"], 1)
        self.assertTrue(out["topShare"])

    def test_pushed_lines_need_push_evidence(self):
        out = self.run_node(f"""
loadConst({json.dumps(BRANCH_CONSTS + ["BRANCH_STATUS_LABELS"])});load({json.dumps(BRANCH_HELPERS + ["branchChipHtml", "branchGitCell", "gitBranchRowHtml", "gitBranchDate"])});
const notPushed={{branch:'feat/x',status:'not_pushed',sessions:1,spend:1,pushes:0,commits:0,added:0,deleted:0,availability:{{cost:true,git:true,commits:false,spend_per_commit:false}}}};
process.stdout.write(JSON.stringify({{
 git:gitBranchRowHtml(notPushed,0),
 byBranch:branchGitCell({{status:'not_pushed',git:{{pushes:0,added:0,deleted:0}}}}),
 pushed:branchGitCell({{status:'pushed',git:{{pushes:2,added:5,deleted:1}}}}),
}}));
""")
        self.assertNotIn("+0", out["git"])
        self.assertIn('data-label="Pushed lines">--</td>', out["git"])
        self.assertNotIn("+0", out["byBranch"])
        self.assertIn("+5 −1", out["pushed"])

    def test_count_label_counts_listed_main_rows(self):
        out = self.branch_view("""(()=>{(0,eval)(extract('branchCountText').replace(/^function ([\\w$]+)/,'globalThis.$1=function $1'));globalThis.countWord=(n,one,many)=>n===1?one:many;
 const shown=view({showMain:true}),hidden=view({});
 return {shown:branchCountText(shown),hidden:branchCountText(hidden),listed:shown.rows.length};})()""")
        self.assertEqual(out["listed"], 4)
        self.assertTrue(out["shown"].startswith("4 of 4 branches"), out["shown"])
        self.assertTrue(out["hidden"].startswith("3 of 3 branches"), out["hidden"])

    def test_collapsed_rows_do_not_point_at_an_unrendered_panel(self):
        out = self.branch_view("""(()=>{
 globalThis.pct=n=>Math.round(n*100)+'%';globalThis.providerColor=id=>`var(--provider-${id})`;globalThis.appFilterLabel=r=>String(r.provider);
 globalThis.metricAvailable=()=>true;globalThis.sessionRowKey=r=>String(r.session||r.id);globalThis.BRANCH_SESSION_LIMIT=40;
 globalThis.BRANCH_STATUS_LABELS={pushed:'Pushed',no_git_evidence:'No Git evidence',in_progress:'In progress',unknown:'--'};
 ['branchRowHtml','branchGitCell','branchWhen','branchChipHtml'].forEach(n=>(0,eval)(extract(n).replace(/^function ([\\w$]+)/,'globalThis.$1=function $1')));
 (0,eval)(constLine('BRANCH_CHIP_ICON'));
 const row=view({}).rows[0];globalThis.branchExpanded='';const closed=branchRowHtml(row,0);
 globalThis.branchExpanded=row.key;const open=branchRowHtml(row,0);return {closed,open};})()""")
        self.assertNotIn("aria-controls", out["closed"])
        self.assertIn("aria-expanded=false", out["closed"])
        self.assertIn("aria-controls=b-sessions-0", out["open"])
        self.assertIn("id=b-sessions-0", out["open"])
        page = Path(DASHBOARD_PAGE).read_text(encoding="utf-8")
        self.assertIn(".branchSession:focus-visible{border-color:var(--line3);outline:2px solid var(--accent);outline-offset:2px}", page)
        self.assertNotIn("branchSession:focus-visible{border-color:var(--line3);outline:none}", page)

    def test_all_history_requests_the_all_time_git_range(self):
        out = self.run_node("""
(0,eval)(constLine('BRANCH_GIT_RANGES'));var branchTime='all',branchGit={range:'',at:0,rows:null,start:0,loading:''},sessionScope='',urls=[];
globalThis.fetch=async url=>{urls.push(url);return {ok:true,json:async()=>({ok:true,branches:{rows:[]}})};};
(0,eval)('globalThis.loadBranchGitStatus=async '+extract('loadBranchGitStatus'));
loadBranchGitStatus().then(()=>process.stdout.write(JSON.stringify({urls,start:branchGit.start,all:BRANCH_GIT_RANGES.all})));
""")
        self.assertEqual(out["all"], "all")
        self.assertEqual(out["urls"], ["/git-delivery?range=all"])
        self.assertEqual(out["start"], 0)

    def test_branch_view_sorting(self):
        out = self.branch_view("""({
 spend:view({sort:'spend'}).rows.map(r=>r.branch),
 sessions:view({sort:'sessions'}).rows.map(r=>r.branch),
 last:view({sort:'last'}).rows.map(r=>r.branch),
 branch:view({sort:'branch'}).rows.map(r=>r.branch),
 unknownGit:branchSpendView(rows,{rangeStart:base.rangeStart,gitRows:null}).rows.map(r=>r.status),
})""")
        self.assertEqual(out["spend"], ["feat/branch-spend", "FIG-7931-goals", "feat/unpriced"])
        self.assertEqual(out["sessions"], ["feat/branch-spend", "FIG-7931-goals", "feat/unpriced"])
        self.assertEqual(out["last"], ["feat/unpriced", "feat/branch-spend", "FIG-7931-goals"])
        self.assertEqual(out["branch"], ["feat/branch-spend", "FIG-7931-goals", "feat/unpriced"], "sorted by the readable name")
        self.assertEqual(out["unknownGit"], ["unknown", "unknown", "unknown"])

    def test_session_branch_lookup_uses_inventory_keys(self):
        out = self.run_node("""
var allSessionInventory=[{id:'s1',key:'tk1',session:'s1.jsonl',branch:'feat/x'},{id:'s2',session:'s2.jsonl'}];
var branchIndexSource=null,branchIndex=new Map();
load(['sessionBranchIndex','sessionBranchFor','mergeAllSessionInventory','compareKeyFor','sessionRowKey']);
process.stdout.write(JSON.stringify({
 byKey:sessionBranchFor({key:'tk1'}),bySession:sessionBranchFor({session:'s1.jsonl'}),byId:sessionBranchFor({id:'s1'}),
 own:sessionBranchFor({id:'zz',branch:'feat/own'}),none:sessionBranchFor({id:'s2'}),
 merged:mergeAllSessionInventory(allSessionInventory,[{id:'s1',key:'tk1',session:'s1.jsonl',cost:9}]).find(r=>r.key==='tk1'),
}));
""")
        self.assertEqual(out["byKey"], "feat/x")
        self.assertEqual(out["bySession"], "feat/x")
        self.assertEqual(out["byId"], "feat/x")
        self.assertEqual(out["own"], "feat/own")
        self.assertEqual(out["none"], "")
        self.assertEqual(out["merged"]["cost"], 9)
        self.assertEqual(out["merged"]["branch"], "feat/x", "live rows keep the inventory branch")

    def test_all_sessions_search_matches_branch(self):
        out = self.run_node("""
load(['allSessionsView','childRootId']);var metricAvailable=()=>true;
const rows=[{id:'1',title:'One',branch:'feat/needle-branch',mtime:5},{id:'2',title:'Two',mtime:5}];
process.stdout.write(JSON.stringify(allSessionsView(rows,{query:'needle'}).rows.map(r=>r.id)));
""")
        self.assertEqual(out, ["1"])

    def test_git_branch_rows_search_and_sort(self):
        out = self.run_node(f"""
loadConst({json.dumps(BRANCH_CONSTS)});load({json.dumps(BRANCH_HELPERS + ["branchMatchesQuery", "gitBranchSortValue", "gitBranchRowsView"])});
const rows=[
 {{branch:'feat/a',status:'pushed',spend:5,sessions:2,pushes:1,commits:2,added:10,deleted:1,spend_per_commit:2.5,last_push:30,availability:{{cost:true,commits:true,spend_per_commit:true}}}},
 {{branch:'FIG-12-b',status:'not_pushed',spend:9,sessions:1,pushes:0,commits:0,added:0,deleted:0,spend_per_commit:null,last_push:null,availability:{{cost:true,commits:false,spend_per_commit:false}}}},
 {{branch:'fix/c',status:'no_git_evidence',spend:null,sessions:4,pushes:null,commits:null,added:null,deleted:null,spend_per_commit:null,last_push:null,availability:{{cost:false,commits:false,spend_per_commit:false}}}},
];
const pick=(o)=>gitBranchRowsView(rows,o).map(r=>r.branch);
process.stdout.write(JSON.stringify({{
 spend:pick({{sort:{{key:'spend',direction:'desc'}}}}),
 spendAsc:pick({{sort:{{key:'spend',direction:'asc'}}}}),
 sessions:pick({{sort:{{key:'sessions',direction:'desc'}}}}),
 perCommit:pick({{sort:{{key:'spend_per_commit',direction:'desc'}}}}),
 lines:pick({{sort:{{key:'lines',direction:'desc'}}}}),
 name:pick({{sort:{{key:'branch',direction:'asc'}}}}),
 search:pick({{query:'fig-12',sort:{{key:'spend',direction:'desc'}}}}),
 searchReadable:pick({{query:'a',sort:{{key:'branch',direction:'asc'}}}}),
}}));
""")
        self.assertEqual(out["spend"], ["FIG-12-b", "feat/a", "fix/c"])
        self.assertEqual(out["spendAsc"], ["feat/a", "FIG-12-b", "fix/c"], "unavailable stays last")
        self.assertEqual(out["sessions"], ["fix/c", "feat/a", "FIG-12-b"])
        self.assertEqual(out["perCommit"], ["feat/a", "FIG-12-b", "fix/c"])
        self.assertEqual(out["lines"], ["feat/a", "FIG-12-b", "fix/c"])
        self.assertEqual(out["name"], ["feat/a", "FIG-12-b", "fix/c"])
        self.assertEqual(out["search"], ["FIG-12-b"])
        self.assertIn("feat/a", out["searchReadable"])

    def test_git_branch_metrics_score_three_down_and_show_unavailable(self):
        out = self.run_node(f"""
loadConst({json.dumps(BRANCH_CONSTS + ["pct", "GIT_BRANCH_HARD_MIN"])});
load(['percentTrendDelta','pointsTrendDelta','gitBranchMetricCards','deliveryPercent']);
const ready={{
 cost_to_ship:{{value:8.7,available:true,branches:4,median_pushed_lines:388.5,comparison:{{change_pct:-12.5}}}},
 unshipped_share:{{value:.05,available:true,in_progress_spend:17.3,in_progress_share:.008,stale_days:7,comparison:{{change_pp:-10.96}}}},
 rework_share:{{value:.335,available:true,estimate:'session_level',comparison:{{change_pp:4.2}}}},
 hard_branches:{{available:true,count:3,spend_share:.237,eligible:23,comparison:{{change_pp:null}}}},
}};
const missing={{
 cost_to_ship:{{value:null,available:false,branches:0,median_pushed_lines:null,comparison:{{change_pct:null}}}},
 unshipped_share:{{value:null,available:false,spend:null,in_progress_spend:null,comparison:{{change_pp:null}}}},
 rework_share:{{value:null,available:false,comparison:{{change_pp:null}}}},
 hard_branches:{{available:false,count:null,spend_share:null,eligible:2,comparison:{{change_pp:null}}}},
}};
process.stdout.write(JSON.stringify({{ready:gitBranchMetricCards(ready,'prior 30 days'),missing:gitBranchMetricCards(missing,'prior 30 days')}}));
""")
        ready = {card["id"]: card for card in out["ready"]}
        self.assertEqual([card["id"] for card in out["ready"]], ["cost", "unshipped", "rework", "hard"])
        self.assertEqual(ready["cost"]["improving"], "down")
        self.assertEqual(ready["unshipped"]["improving"], "down")
        self.assertEqual(ready["rework"]["improving"], "down")
        self.assertEqual(ready["hard"]["improving"], "down", "hard-to-finish is scored on its spend share")
        self.assertEqual(ready["cost"]["value"], "$8.70")
        self.assertIn("389", ready["cost"]["meta"])
        self.assertEqual(ready["cost"]["delta"]["direction"], "down")
        self.assertIn("12.5%", ready["cost"]["delta"]["text"])
        self.assertEqual(ready["unshipped"]["delta"]["direction"], "down")
        self.assertIn("11.0 pts", ready["unshipped"]["delta"]["text"])
        self.assertEqual(ready["rework"]["delta"]["direction"], "up")
        self.assertIn("estimate", ready["rework"]["label"].lower() + ready["rework"]["meta"].lower())
        self.assertEqual(ready["hard"]["value"], "3")
        self.assertIn("24%", ready["hard"]["meta"])
        for card in out["missing"]:
            with self.subTest(card=card["id"]):
                self.assertEqual(card["value"], "--")
                self.assertTrue(card["reason"])
                self.assertEqual(card["delta"]["direction"], "")
        self.assertIn("5", {c["id"]: c for c in out["missing"]}["hard"]["reason"])


class BranchMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = Path(DASHBOARD_PAGE).read_text(encoding="utf-8")

    def test_by_branch_scope_is_registered_between_compare_and_subagents(self):
        self.assertIn("const SESSION_SCOPES=['current','all','compare','branches','subagents'];", self.page)
        compare = self.page.index("id=session-scope-compare")
        branches = self.page.index("id=session-scope-branches")
        subagents = self.page.index("id=session-scope-subagents")
        self.assertLess(compare, branches)
        self.assertLess(branches, subagents)
        tab = re.search(r"<button class=tab id=session-scope-branches [^>]*>", self.page).group(0)
        self.assertIn("role=tab", tab)
        self.assertIn("aria-selected=false", tab)
        self.assertIn("aria-controls=branch-spend", tab)
        self.assertIn("data-session-scope=branches", tab)
        self.assertIn("<span class=tabLabel>By branch</span>", self.page)
        self.assertIn("<section id=branch-spend role=tabpanel aria-labelledby=session-scope-branches hidden>", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_scope_routes_and_labels(self):
        script = HARNESS + r"""
(0,eval)(page.match(/^const SESSION_SCOPES=.*$/m)[0].replace('const ','var '));
load(['normalizeSessionScope','sessionScopeRoute','sessionScopeLabel']);
process.stdout.write(JSON.stringify({routes:SESSION_SCOPES.map(sessionScopeRoute),labels:SESSION_SCOPES.map(sessionScopeLabel),bogus:sessionScopeRoute('nope')}));
"""
        result = subprocess.run(["node", "-e", script, str(DASHBOARD_PAGE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["routes"], ["sessions", "sessions-all", "sessions-compare", "sessions-branches", "sessions-subagents"])
        self.assertEqual(out["labels"], ["Current sessions", "All sessions", "Compare", "By branch", "Subagents"])
        self.assertEqual(out["bogus"], "sessions")

    def test_router_keeps_legacy_routes_and_adds_branches(self):
        self.assertIn("h==='sessions-branches'", self.page)
        self.assertIn("h==='sessions-compare'?'compare':h==='sessions-branches'?'branches':'current'", self.page)
        for legacy in ("h==='logs'", "h==='current-sessions'", "h==='subagents'||h==='sessions-subagents'", "h==='sessions-all'"):
            self.assertIn(legacy, self.page)

    def test_session_tabs_fit_five_columns(self):
        self.assertIn(
            "body.sessionRoute #view-session .sessionScopeTabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr))",
            self.page,
        )
        self.assertEqual(self.page.count('data-session-scope='), 5)

    def test_by_branch_toolbar_matches_all_sessions(self):
        start = self.page.index("<section id=branch-spend")
        block = self.page[start:self.page.index("</section>", start)]
        for expected in (
            'class="globalControls logsToolbar"', "id=b-search", 'placeholder="Branch name or issue key"',
            "id=b-app", "id=b-project", "id=b-status", "id=b-main", "id=b-sort", "id=b-clear", "id=b-count",
            "<option value=no_git_evidence>No Git evidence</option>", "<option value=in_progress>In progress</option>",
            "data-bsort=spend", "data-bsort=sessions", "data-bsort=last", "data-bsort=branch",
            'class="logStat metric"', "id=b-total-feature", "id=b-total-main", "id=b-total-none", "id=b-total-top",
        ):
            self.assertIn(expected, block)
        all_time = re.search(r"<select class=filterSelect id=g-time[^>]*>(.*?)</select>", self.page).group(1)
        branch_time = re.search(r"<select class=filterSelect id=b-time[^>]*>(.*?)</select>", block).group(1)
        self.assertEqual(branch_time, all_time)
        for key in ("tm_branch_search", "tm_branch_app", "tm_branch_project", "tm_branch_time", "tm_branch_status",
                    "tm_branch_main", "tm_branch_sort"):
            self.assertIn(key, self.page)

    def test_branch_chip_is_one_zero_specificity_component(self):
        self.assertIn(":where(.branchChip){", self.page)
        self.assertNotRegex(self.page, r"(?m)^\.branchChip\{")
        self.assertEqual(self.page.count("function branchChipHtml("), 1)

    def test_branch_chip_placements(self):
        cards = self.page[self.page.index("function renderCurrentSessions("):]
        self.assertIn("currentSessionBranchAnchor", cards)
        self.assertIn("branchChipHtml(", cards[:cards.index("function keepCurrentSessionFilterFocusVisible(")])
        self.assertIn("<span class=repo id=preview-project>--</span>\n   <span id=preview-branch hidden></span>", self.page)
        row = self.page[self.page.index("function logRowContent("):self.page.index("const CHILD_SECTION_LIMIT")]
        self.assertIn("branchChipHtml(", row)
        self.assertIn("document.addEventListener('click',handleBranchChipClick,true);", self.page)

    def test_card_branch_chip_is_a_sibling_not_nested_in_the_card_button(self):
        render = self.page[self.page.index("function currentSessionBranchSibling("):]
        render = render[:render.index("\n}") + 2]
        self.assertIn("branchChipHtml(", render)
        cards = self.page[self.page.index("function renderCurrentSessions("):]
        template = cards[cards.index("return `<div class=currentSessionSlot"):]
        template = template[:template.index("`;")]
        button = template[template.index("<button class=\"card currentSessionCard"):template.index("</button>${branchSibling}")]
        self.assertNotIn("branchChipHtml(", button)
        self.assertIn("</button>${branchSibling}${desktopLink}", template)

    def test_git_branches_card_precedes_projects_with_scroll_table(self):
        git = self.page[self.page.index("<div class=view id=view-git>"):self.page.index("<div class=view id=view-learn>")]
        self.assertLess(git.index("id=d-branches"), git.index('class="card deliveryProjects"'))
        for expected in ("id=d-branch-search", "id=d-branch-count", "id=d-branch-caption", "id=d-branch-table",
                         'class="deliveryTableWrap deliveryBranchTableWrap" id=d-branch-wrap',
                         "data-delivery-branch-sort=spend", "data-delivery-branch-sort=spend_per_commit",
                         "data-delivery-branch-sort=last_push", "id=d-branch-cost-change", "id=d-branch-unshipped-change",
                         "id=d-branch-rework-change", "id=d-branch-hard-change"):
            self.assertIn(expected, git)
        self.assertIn(".deliveryBranchTableWrap{max-height:", self.page)
        self.assertIn(".deliveryBranchTableWrap .tbl th{position:sticky;top:0", self.page)
        self.assertIn("overscroll-behavior:contain", self.page)
        self.assertIn("function fitGitBranchRows(", self.page)
        self.assertIn("GIT_BRANCH_VISIBLE_ROWS=5", self.page)


if __name__ == "__main__":
    unittest.main()
