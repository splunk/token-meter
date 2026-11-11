import unittest
import json
import re
import shutil
import subprocess
from pathlib import Path
from tests.dashboard_page import DASHBOARD_PAGE
from tests.dashboard_layout_support import DashboardLayoutCase


class DashboardToolsLayoutTests(DashboardLayoutCase):
    def test_tools_leads_with_stats_review_queue_and_visible_inventory(self):
        self.assertRegex(self.page, r"id=tab-capabilities[^>]*>.*?<span class=tabLabel>Tools</span>")
        tools = self.page.split("id=view-capabilities", 1)[1].split(
            "id=view-settings", 1
        )[0]
        for marker in (
            'aria-label="Capability overview"', "id=c-stat-tools",
            "id=c-stat-mcps", "id=c-stat-skills", "data-cap-jump=tool",
            "id=c-review-queue", "id=c-review-list",
            "id=c-selection-bar", "id=c-disable-selected",
            "id=c-inventory", "id=c-clear-filters", "id=c-active-filters",
            'aria-label="Prompt-load evidence"',
        ):
            self.assertIn(marker, tools)
        self.assertLess(tools.index('aria-label="Capability overview"'), tools.index("id=c-review-queue"))
        self.assertLess(tools.index("id=c-review-queue"), tools.index("id=c-inventory"))
        self.assertRegex(tools, r"<section class=\"card capInventory\" id=c-inventory")
        self.assertNotIn('class="card capInventory" id=c-inventory-details', tools)
        self.assertNotIn("id=c-catalog-details", tools)
        self.assertIn('class="capPageHead spectrumPageHead"', tools)
        self.assertIn("class=spectrumPageHeadCopy", tools)
        tools_head = tools.split('<section class="card capStats"', 1)[0]
        for removed in (
            "hasPageActions", "spectrumPageActions", "capFreshness",
            "c-evidence-status", "c-evidence-updated",
        ):
            self.assertNotIn(removed, tools_head)
        self.assertNotIn("c-evidence-status", self.page)
        self.assertNotIn("c-evidence-updated", self.page)
        for removed in (
            "id=c-decision-panel", "id=c-opt-enabled", "id=c-opt-used",
            "id=c-opt-review", "id=c-mcp-observed",
            'aria-label="Capability evidence summary"', "id=c-review-filter",
            "id=c-browse-all",
        ):
            self.assertNotIn(removed, tools)
        self.assertIn('aria-label="Capability type"', self.page)
        self.assertIn('aria-label="Evidence and configuration"', self.page)
        self.assertIn('aria-label="Search installed capabilities"', self.page)
        self.assertIn("button.setAttribute('aria-pressed'", self.page)
        self.assertIn("function renderCapabilityReviewQueue(cap", self.page)
        self.assertIn("function unusedReviewGroups(cap)", self.page)
        self.assertIn("if(!cap)return;", self.page)
        self.assertIn("fetch('/capabilities/inventory'", self.page)
        self.assertIn("CAPABILITY_PAGE_SIZE=100", self.page)
        self.assertIn("id=c-mobile-sort", self.page)
        self.assertIn("function openCapabilityInventory(type='all')", self.page)
        self.assertIn("button.onclick=()=>openCapabilityInventory(button.dataset.capJump)", self.page)
        self.assertIn("function clearCapabilityFilter(key)", self.page)
        self.assertIn("row.measurement==='instruction'?'Instruction-only':'Evidence unavailable'", self.page)
        self.assertIn("return row.enabled===true?'Enabled':row.enabled===false?'Disabled':'Unknown'", self.page)
        self.assertIn("const selectedCapabilityIds=new Set()", self.page)
        self.assertIn("openSelectedDisableDialog", self.page)
        self.assertIn("capabilityRuntime='all'", self.page)
        self.assertIn("lastCapabilityReviewRevision", self.page)
        self.assertIn("cap.review_revision", self.page)
        self.assertIn("cap.inventory_revision", self.page)
        self.assertIn("setCapabilityAvailability('stale'", self.page)
        self.assertIn("data-label=\"Configuration\"", self.page)
        self.assertIn("data-label=\"Evidence\"", self.page)
        self.assertNotIn("Loaded ·", self.page)
        self.assertNotIn("Disable all unused", self.page)
        self.assertNotIn("if(row.unmeasurable) return '<span class=subline>not measurable</span>'", self.page)

    def test_skill_pack_changes_confirm_exact_control_and_use_verified_state(self):
        self.assertIn("id=cap-dialog", self.page)
        self.assertIn("control_id:controlId", self.page)
        self.assertIn("result.capabilities||cap", self.page)
        self.assertIn("Setting verified.", self.page)
        self.assertIn("row.reviewable!==false", self.page)
        self.assertNotIn("...group,id:group.item_id", self.page)

    def test_sessions_show_skill_and_mcp_capability_counts(self):
        self.assertIn("return `${f(counts.used)}/${counts.loaded==null?'—':f(counts.loaded)}${counts.basis==='configured'?'*':''}`;", self.page)
        self.assertIn('</div>${identityAction}</div>\n  <div class=sactions>${sessionDeleteAvailable(s,renderedAllSessionActions)?', self.page)
        self.assertNotIn('<div class="badge tok">', self.page)
        self.assertIn('class="sessionDelete sessionDeleteIcon" data-delete-session="${esc(sessionRowKey(s))}" type=button aria-label="Delete session" title="Delete session"><svg', self.page)
        self.assertIn('<span class="currentSessionCaps mono" title="${esc(CAPABILITY_SUMMARY_TIP)}">${esc(capabilitySummaryText(row.capabilities))}</span>', self.page)
        self.assertIn('<div class=meta title="${esc(`${waitText} · ${avg}/exec${speed} · ${CAPABILITY_SUMMARY_TIP}`)}">${esc(s.project||\'local\')} · ${esc(s.last||s.start||\'\')} · ${esc(capabilitySummaryText(s.capabilities))}${s.branch?` ${branchChipHtml(s.branch,{project:s.project})}`:\'\'}</div>', self.page)
        self.assertIn("s.throughput,s.cost_approx,s.capabilities,childAgentsFor(s)", self.page)
        self.assertIn("<div class=sbar><i></i></div>${childAgentSubsection(s)}</div>", self.page)
        self.assertIn('id=ov-skills>--</div>', self.page)
        self.assertIn('id=ov-mcp>--</div>', self.page)
        self.assertIn("counts.loaded==null?'loaded list not recorded'", self.page)
        self.assertIn("* means configured now, not recorded by this session.", self.page)

    def test_selected_candidate_action_has_confirmation_and_exact_control_ids(self):
        self.assertIn("id=c-disable-selected", self.page)
        self.assertIn("id=bulk-dialog", self.page)
        self.assertIn("/capability/disable-unused", self.page)
        self.assertIn('data-cap-select="${esc(row.id)}"', self.page)
        self.assertIn("selectedCapabilityIds.add(input.dataset.capSelect)", self.page)
        self.assertIn("unusedReviewGroups(cap).filter(row=>selectedCapabilityIds.has(row.id))", self.page)
        self.assertIn("pendingBulkDisable={controlIds,fingerprints:", self.page)
        self.assertIn("capabilityGroupFingerprint(row)", self.page)
        self.assertIn("$('bulk-dialog-controls').textContent=controlIds.join('\\n')", self.page)
        self.assertIn("control_ids:controlIds", self.page)
        self.assertIn("Review evidence changed", self.page)
        self.assertIn('aria-label="Select ${esc(name)}"', self.page)
        self.assertIn("MCP servers, runtime packs, built-ins, standalone skills, and used groups are excluded.", self.page)
        self.assertIn("Disable selected", self.page)
        self.assertNotIn("id=c-disable-unused", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dashboard JavaScript")
    def test_capability_review_queue_renders_one_expandable_row_per_pack(self):
        script = f"""
const fs=require('fs');
const page=fs.readFileSync({json.dumps(str(Path(DASHBOARD_PAGE)))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const f=n=>Math.round(n||0).toLocaleString('en-US');
const countWord=(n,one,many=one+'s')=>Number(n)===1?one:many;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const compactNumber=n=>String(n);
const els={{}};
const el=id=>els[id]||(els[id]={{id,textContent:'',innerHTML:'',hidden:false,disabled:false,title:'',classList:{{toggle(){{}}}},matches:()=>false,contains:()=>false,querySelectorAll:()=>[],querySelector:()=>null}});
const $=el;
const document={{activeElement:null}};
const selectedCapabilityIds=new Set(['codex:aws']);
const openCapabilityReviewIds=new Set();
let capabilityEvidenceState='current',lastCapabilityReviewRevision='',pendingCapabilityReview=null;
const openCapabilityInventory=()=>{{}};
eval(['capabilityPackName','capabilityGroupFingerprint','updateCapabilitySelection','unusedReviewGroups'].map(extract).join('\\n'));
eval(page.slice(page.indexOf('function renderCapabilityReviewQueue('),page.indexOf('function renderCapabilityTable(')));
const group=(id,plugin,runtime,extra={{}})=>({{id,plugin_id:plugin,runtime,mutable:true,enabled:true,used:false,unmeasurable:false,scanned_sessions:142,members:['a','b','c','d','e','f'],setting_path:'~/.codex/config.toml',...extra}});
const render=groups=>{{
 lastCapabilityReviewRevision='';
 renderCapabilityReviewQueue({{review_revision:String(Math.random()),summary:{{optional:{{review_candidates:groups.map(g=>g.id)}}}},control_groups:groups}},{{force:true}});
 return {{html:el('c-review-list').innerHTML,caveat:el('c-review-detail').textContent}};
}};
const same=render([group('codex:aws','aws-skills@market','Codex'),group('codex:gcp','gcp-skills','Codex',{{unused_eager_definition_tokens:12345,members:['solo']}})]);
const mixed=render([group('codex:aws','aws-skills','Codex'),group('claude:k8s','k8s','Claude')]);
process.stdout.write(JSON.stringify({{same,mixed}}));
"""
        payload = json.loads(subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True,
        ).stdout)
        same = payload["same"]["html"]
        rows = re.findall(r"<article class=capReviewItem[^>]*>.*?</article>", same, re.S)
        self.assertEqual(len(rows), 2)
        self.assertEqual(same.count("class=capReviewHead"), 1)
        self.assertLess(same.index("class=capReviewHead"), same.index("<article"))
        first = rows[0]
        summary = re.search(r"<summary class=capReviewCols[^>]*>(.*?)</summary>", first, re.S)
        self.assertIsNotNone(summary)
        summary_html = summary.group(1)
        self.assertIn("<strong>aws-skills</strong>", summary_html)
        self.assertIn("Codex · User-installed", summary_html)
        self.assertIn("0 uses · 142 logs", summary_html)
        self.assertIn('title="Not measured"', summary_html)
        self.assertIn(">—<", summary_html)
        self.assertNotIn(">0<", summary_html)
        self.assertIn("6 skills", summary_html)
        self.assertIn('class="stateDot unknown"', summary_html)
        # The checkbox sits outside the summary so selecting never toggles the row.
        self.assertNotIn("data-cap-select", summary_html)
        self.assertNotIn("<input", first.split("</details>", 1)[0])
        self.assertIn('data-cap-select="codex:aws" aria-label="Select aws-skills" checked', first)
        detail = first.split("</summary>", 1)[1].split("</details>", 1)[0]
        for marker in ("class=capReviewFacts", "0 activations · 142 Codex logs",
                       "Not measured", "Exact control", "codex:aws", "~/.codex/config.toml"):
            self.assertIn(marker, detail)
        self.assertIn("12,345", rows[1])
        self.assertIn("1 skill<", rows[1])
        self.assertNotIn('aria-label="Select gcp-skills" checked', rows[1])
        # One runtime: the restart note is shown once in the header, not per row.
        self.assertNotIn("to apply", same)
        self.assertIn("Restart Codex to apply", payload["same"]["caveat"])
        mixed = payload["mixed"]["html"]
        self.assertEqual(mixed.count("Restart Codex to apply"), 1)
        self.assertEqual(mixed.count("Restart Claude to apply"), 1)
        self.assertNotIn("Restart", payload["mixed"]["caveat"])

    def test_capability_polling_uses_stable_revisions_and_lazy_inventory(self):
        review = self.page.split("function renderCapabilityReviewQueue", 1)[1].split(
            "function renderCapabilityTable", 1
        )[0]
        inventory = self.page.split("function renderCapabilityTable", 1)[1].split(
            "let pendingCapabilityAction", 1
        )[0]
        self.assertIn("revision===lastCapabilityReviewRevision", review)
        self.assertIn("lastCapabilityReviewRevision=revision", review)
        self.assertIn("if(!cap)return;", inventory)
        self.assertNotIn("c-inventory-details", inventory)
        self.assertNotIn("$('c-inventory-details').addEventListener('toggle'", self.page)
        self.assertIn("fetch('/capabilities/inventory',{cache:'no-store'})", self.page)
        self.assertIn("countWord(inventoryCount,'capability','capabilities')", self.page)
        self.assertIn("capabilityInventorySnapshot.inventory_revision===cap.inventory_revision", self.page)
        self.assertIn("renderCapabilityTable(merged,{force});", self.page)
        self.assertIn("if(inventory)renderCapabilityTable(inventory,{force:true});", self.page)
        self.assertIn("capabilityLastGoodAt=Date.now()", self.page)
        self.assertIn("setCapabilityAvailability('unavailable'", self.page)
        self.assertIn("setCapabilityAvailability('stale'", self.page)
        self.assertIn("capabilityEvidenceState!=='current'", self.page)
        self.assertIn("Confirmation closed", self.page)
        self.assertIn("if(capabilitySort==='returned')capabilitySort='use'", self.page)


if __name__ == "__main__":
    unittest.main()
