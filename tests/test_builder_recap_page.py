import unittest
import http.client
import json
import re
import shutil
import subprocess
import tempfile
import threading
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
import meter


class BuilderRecapStandalonePageTests(unittest.TestCase):
    def request(self, method, path):
        server = meter.TokenMeterHTTPServer(("127.0.0.1", 0), meter.H)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5,
            )
            conn.request(method, path)
            response = conn.getresponse()
            body = response.read()
            result = {
                "status": response.status,
                "type": response.getheader("Content-Type"),
                "length": response.getheader("Content-Length"),
                "cache": response.getheader("Cache-Control"),
                "body": body,
            }
            conn.close()
            return result
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_performance_document_routes_and_manifest(self):
        manifest = Path(meter.__file__).with_name("runtime-manifest.txt").read_text()
        self.assertIn("required performance.html", manifest.splitlines())

        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "performance.html"
            page.write_text("<!doctype html><title>Builder recap</title>")
            with mock.patch.object(
                    meter, "performance_page_path", return_value=str(page),
                    create=True):
                direct = self.request("GET", "/performance")
                alias = self.request("GET", "/performance.html")
                head = self.request("HEAD", "/performance")

            for response in (direct, alias):
                self.assertEqual(response["status"], 200)
                self.assertEqual(response["type"], "text/html; charset=utf-8")
                self.assertEqual(response["cache"], "no-store, max-age=0")
                self.assertEqual(response["body"], page.read_bytes())
            self.assertEqual(head["status"], 200)
            self.assertEqual(head["body"], b"")
            self.assertEqual(head["length"], str(page.stat().st_size))
            self.assertEqual(head["cache"], "no-store, max-age=0")

            with mock.patch.object(
                    meter, "performance_page_path", return_value=None,
                    create=True):
                missing = self.request("GET", "/performance")
            self.assertEqual(missing["status"], 503)
            self.assertIn(b"performance.html is missing", missing["body"])
            self.assertNotIn(str(tmp).encode(), missing["body"])

    def test_standalone_page_declares_inline_svg_favicon(self):
        class IconInventory(HTMLParser):
            def __init__(self):
                super().__init__()
                self.icons = []

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "link" and "icon" in attributes.get("rel", "").split():
                    self.icons.append(attributes)

        page = Path(meter.__file__).with_name("performance.html").read_text()
        markup = IconInventory()
        markup.feed(page)

        self.assertEqual(len(markup.icons), 1)
        self.assertEqual(markup.icons[0]["type"], "image/svg+xml")
        self.assertTrue(markup.icons[0]["href"].startswith("data:image/svg+xml,"))

    def test_standalone_studio_has_the_approved_controls_and_no_remote_assets(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        self.assertTrue(page_path.is_file())
        page = page_path.read_text()
        for marker in (
            'href="/#subagents"', 'href="/#efficiency"', 'id=range-controls', 'data-range=7',
            'data-range=30', 'data-range=90',
            '<span>Name</span><input id=builder-name maxlength=40',
            'id=include-usage type=checkbox', 'id=reset type=button',
            'id=retry type=button hidden',
            'id=download type=button disabled>Download PNG</button>',
            'id=poster width=1080 height=1350 role=img',
            'id=status role=status aria-live=polite',
            'function drawBuilderRecap(', 'function loadRecap(',
            '/builder-recap?range=', 'github.com/splunk/token-meter',
            '@media(max-width:1024px)',
        ):
            self.assertIn(marker, page)
        self.assertEqual(page.count("function drawBuilderRecap("), 1)
        self.assertNotRegex(page, r'<(?:script|img|link)[^>]+(?:src|href)=["\']https?://')
        self.assertNotIn("foreignObject", page)
        # Only the shared theme boot script may read storage; the studio never persists builder input.
        self.assertNotIn("localStorage", re.sub(r"<script id=theme-boot>.*?</script>", "", page, flags=re.S))
        self.assertNotIn("Display name", page)
        self.assertNotIn("id=spotlight", page)
        self.assertNotIn("id=include-git", page)

    def test_standalone_studio_uses_the_spectrum_shell_and_compact_editor(self):
        class MarkupInventory(HTMLParser):
            def __init__(self):
                super().__init__()
                self.body_classes = set()
                self.by_id = {}

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "body":
                    self.body_classes.update(attributes.get("class", "").split())
                if attributes.get("id"):
                    self.by_id[attributes["id"]] = (tag, attributes)

        page = Path(meter.__file__).with_name("performance.html").read_text()
        markup = MarkupInventory()
        markup.feed(page)

        self.assertTrue({"spectrumApp", "performanceApp"}.issubset(markup.body_classes))
        self.assertEqual(markup.by_id["performance-rail"][0], "aside")
        self.assertEqual(
            markup.by_id["performance-rail"][1]["aria-label"],
            "Token Meter navigation",
        )
        self.assertEqual(markup.by_id["performance-navigation"][0], "nav")
        performance = markup.by_id["tab-performance"]
        self.assertEqual(performance[0], "a")
        self.assertEqual(performance[1]["href"], "/performance")
        self.assertEqual(performance[1]["aria-current"], "page")
        self.assertEqual(markup.by_id["performance-surface"][0], "main")
        self.assertEqual(markup.by_id["performance-header"][0], "header")
        self.assertIn("<title>Token Meter — Performance card</title>", page)
        self.assertIn("<h1>Performance card</h1>", page)
        self.assertIn("<span class=railLabel>Performance</span>", page)
        self.assertNotIn("headerMeta", page)
        self.assertNotIn("<strong>4:5</strong>", page)
        self.assertEqual(markup.by_id["performance-controls"][0], "section")
        self.assertEqual(
            markup.by_id["performance-controls"][1]["aria-label"],
            "Performance controls",
        )
        self.assertEqual(markup.by_id["performance-preview"][0], "section")
        logo = markup.by_id["performance-card-logo"]
        self.assertEqual(logo[0], "img")
        self.assertEqual(
            logo[1]["src"], "/assets/brand/logo-splunk-by-cisco-rgb-w.png",
        )
        self.assertIn("hidden", logo[1])
        self.assertIn("<div class=studioWorkspace>", page)
        workspace_start = page.index("<div class=studioWorkspace>")
        controls_start = page.index(
            "<section class=controlDock id=performance-controls",
        )
        preview_start = page.index(
            "<section class=stage id=performance-preview",
        )
        self.assertLess(workspace_start, controls_start)
        self.assertLess(controls_start, preview_start)
        self.assertIn(
            ".studioWorkspace{display:grid;grid-template-columns:220px minmax(0,1fr)",
            page,
        )
        self.assertNotIn("evidence-note", markup.by_id)
        self.assertEqual(markup.by_id["status"][1]["role"], "status")
        primary = page.split("<div class=railPrimary>", 1)[1].split("</div>", 1)[0]
        self.assertLess(primary.index('href="/#models"'), primary.index('href="/#subagents"'))
        self.assertLess(primary.index('href="/#subagents"'), primary.index('href="/#efficiency"'))
        self.assertLess(primary.index('href="/#efficiency"'), primary.index('href="/#work"'))
        self.assertLess(primary.index('href="/#work"'), primary.index('href="/#git"'))
        self.assertLess(primary.index('href="/#git"'), primary.index("id=tab-performance"))
        # Work appears only where the dashboard shows it.
        self.assertIn('id=rail-work href="/#work" aria-label=Work title=Work hidden>', primary)
        self.assertIn("$('rail-work').hidden=payload?.settings?.supported!==true;", page)
        self.assertNotIn("Make it yours.", page)
        self.assertNotIn("Builder Recap Studio", page)

    def assert_measured_grid(self, cells):
        """Equal columns span 72-1008 exactly and share every text baseline."""
        self.assertTrue(cells)
        self.assertEqual(cells[0]["x"], 72)
        self.assertEqual(cells[-1]["x"] + cells[-1]["width"], 1008)
        self.assertLessEqual(max(c["width"] for c in cells) - min(c["width"] for c in cells), 1)
        gaps = [b["x"] - (a["x"] + a["width"]) for a, b in zip(cells, cells[1:])]
        if gaps:
            self.assertLessEqual(max(gaps) - min(gaps), 1)
        for key in ("valueY", "labelY", "subY", "size"):
            self.assertEqual(len({cell[key] for cell in cells}), 1, key)
        for cell in cells:
            self.assertIsNotNone(cell["value"], cell)
            self.assertIsNotNone(cell["label"], cell)
            self.assertIn(f"{cell['size']}px", cell["font"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for canvas verification")
    def test_standalone_canvas_formats_and_aligns_metric_values(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
for(const name of ['safeArray','sanitizeBuilderName','compactNumber','durationLabel','formatStat','formatSpend','ellipsize','fitText','gradientFill','radialFill','fillRoundedRect','setTracking','cardPalette','ditherRegion','supportingStats','inkX','posterStatLimit','defaultPosterStats','posterStatCatalog','findRecapStat','posterStatCells','resolveHeadline','paperLayout','headlineBandHeight','statRowHeight','usageBlockHeight','drawHeadlineBand','drawStatRow','drawUsageGroup','drawBuilderRecap'])eval(extract(name));
class Gradient{{constructor(kind,coords){{this.kind=kind;this.coords=coords;this.stops=[];}}addColorStop(offset,color){{this.stops.push([offset,color]);}}}}
class Context{{constructor(){{this.ops=[];this.font='';}}fillRect(...v){{this.ops.push(['rect',this.fillStyle,...v]);}}fillText(...v){{this.ops.push(['text',String(v[0]),...v.slice(1),this.font]);}}drawImage(image,...v){{this.ops.push(['image',image.src,...v]);}}createLinearGradient(...c){{return new Gradient('linear',c);}}createRadialGradient(...c){{return new Gradient('radial',c);}}measureText(v){{const size=Number((/([0-9.]+)px/.exec(this.font)||[])[1])||14,narrow=/Narrow|Condensed/.test(this.font);return {{width:String(v).length*size*(narrow?.45:.55)}};}}beginPath(){{}}arc(){{}}stroke(){{}}save(){{}}restore(){{}}translate(){{}}rotate(){{}}}}
class Canvas{{constructor(){{this.ctx=new Context();this.attrs={{}};}}getContext(){{return this.ctx;}}setAttribute(k,v){{this.attrs[k]=String(v);}}}}
const payload={{range_days:30,current:{{end_day:'2026-09-13'}},spotlights:[
 {{id:'lines_pushed',available:true,current:184392,unit:'lines'}},
 {{id:'efficiency_change',label:'Output efficiency',available:true,current:2680,unit:'output tokens/$'}},
 {{id:'speed_champion',label:'Fastest model',available:true,current:42.8,unit:'output tokens/s',leaders:[{{model:'gpt-5.3'}}]}},
 {{id:'marathon_session',label:'Longest session',available:true,current:24120,unit:'seconds'}},
 {{id:'build_streak',available:true,current:14,unit:'days'}}
],supporting:[{{id:'sessions',label:'AI coding sessions',available:true,current:126,unit:'sessions'}},{{id:'covered_spend',label:'Covered equivalent spend',family:'cost',available:true,current:2176.88,previous:null,delta_pct:null,unit:'USD'}}],activity_days:Array.from({{length:30}},(_,i)=>({{recorded_session:i<24}})),usage:{{agents:[],models:[]}}}};
const canvas=new Canvas();
function cellsOf(target,result){{return result.layout.stats.map(cell=>{{const at=(y)=>target.ctx.ops.find(op=>op[0]==='text'&&op[2]===cell.x&&op[3]===y);const value=at(cell.valueY),label=at(cell.labelY),sub=at(cell.subY);return {{id:cell.id,x:cell.x,width:cell.width,valueY:cell.valueY,labelY:cell.labelY,subY:cell.subY,size:cell.size,value:value&&value[1],font:value&&value[4],label:label&&label[1],sub:sub?sub[1]:''}};}});}}
const mainResult=drawBuilderRecap(canvas,payload,{{name:'Ada Builder',includeUsage:true,logo:{{src:'/assets/brand/logo-splunk-by-cisco-rgb-w.png',complete:true,naturalWidth:1024}}}});
const textOps=canvas.ctx.ops.filter(op=>op[0]==='text');
const missingPayload=JSON.parse(JSON.stringify(payload));missingPayload.supporting=[];const missingCanvas=new Canvas(),missingResult=drawBuilderRecap(missingCanvas,missingPayload,{{name:'',includeUsage:true}});
const unavailableSpeedPayload=JSON.parse(JSON.stringify(payload));unavailableSpeedPayload.spotlights.find(stat=>stat.id==='speed_champion').available=false;const unavailableSpeedCanvas=new Canvas(),unavailableSpeedResult=drawBuilderRecap(unavailableSpeedCanvas,unavailableSpeedPayload,{{name:'',includeUsage:true}});
const pickedCanvas=new Canvas(),pickedResult=drawBuilderRecap(pickedCanvas,payload,{{name:'',includeUsage:true,stats:['marathon_session','tool_calls','active_days']}});
const sixCanvas=new Canvas(),sixResult=drawBuilderRecap(sixCanvas,payload,{{name:'',includeUsage:true,stats:['active_days','efficiency_change','speed_champion','sessions','covered_spend','marathon_session']}});
const longPayload=JSON.parse(JSON.stringify(payload));longPayload.supporting.find(stat=>stat.id==='sessions').current=1.2345678901234567e20;const longCanvas=new Canvas(),longResult=drawBuilderRecap(longCanvas,longPayload,{{name:'',includeUsage:true}});
class InkContext extends Context{{measureText(v){{return {{...super.measureText(v),actualBoundingBoxLeft:-6,actualBoundingBoxRight:-3}};}}}}
const inkCanvas=new Canvas();inkCanvas.ctx=new InkContext();drawBuilderRecap(inkCanvas,payload,{{name:'',includeUsage:true}});const inkOps=inkCanvas.ctx.ops.filter(op=>op[0]==='text'&&op[3]===486).map(op=>[op[1],op[2]]);
console.log(JSON.stringify({{texts:textOps.map(op=>op[1]),logos:canvas.ctx.ops.filter(op=>op[0]==='image'),cells:cellsOf(canvas,mainResult),missingCells:cellsOf(missingCanvas,missingResult),unavailableSpeedCells:cellsOf(unavailableSpeedCanvas,unavailableSpeedResult),pickedCells:cellsOf(pickedCanvas,pickedResult),pickedTexts:pickedCanvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]),sixCells:cellsOf(sixCanvas,sixResult),longCells:cellsOf(longCanvas,longResult),inkOps,ghost:textOps.filter(op=>String(op[4]).includes('330px')),aria:canvas.attrs['aria-label'],missingAria:missingCanvas.attrs['aria-label'],missingTexts:missingCanvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]),commitOp:textOps.find(op=>op[2]===1008&&op[3]===486)}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        cells = rendered["cells"]
        self.assertEqual(
            [(cell["label"], cell["value"]) for cell in cells],
            [("ACTIVE DAYS", "24/30"), ("OUTPUT EFFICIENCY", "2.68 K/$"),
             ("FASTEST MODEL", "42.8 TOK/S"), ("AI SESSIONS", "126"),
             ("EQUIV. SPEND", "$2.18K")],
        )
        self.assert_measured_grid(cells)
        self.assertEqual([cell["sub"] for cell in cells],
                         ["14 DAY STREAK", "", "GPT-5.3", "", "ESTIMATE"])
        self.assertIn("30D", rendered["texts"])
        self.assertEqual(rendered["ghost"], [])
        visible = " ".join(rendered["texts"])
        self.assertNotIn("LONGEST SESSION", visible)
        self.assertNotIn("6H 42M", visible)
        self.assertEqual(rendered["texts"].count("LINES PUSHED"), 1)
        self.assertNotIn("LINES", rendered["texts"])
        self.assertEqual(rendered["logos"], [[
            "image", "/assets/brand/logo-splunk-by-cisco-rgb-w.png",
            72, 46, 148, 54,
        ]])
        self.assertIn("TOKEN METER", rendered["texts"])
        self.assertIn("PERFORMANCE CARD", rendered["texts"])
        self.assertNotIn("TOKEN METER / PERFORMANCE", rendered["texts"])
        self.assertNotIn("2.68K", rendered["texts"])
        self.assertNotIn("Longest session", rendered["aria"])
        self.assertIn("AI sessions 126.", rendered["aria"])
        self.assertIn(
            "Covered equivalent spend $2.18K, an estimate.", rendered["aria"],
        )
        self.assertIn("AI sessions unavailable.", rendered["missingAria"])
        self.assertIn(
            "Covered equivalent spend unavailable.", rendered["missingAria"],
        )
        # Unavailable evidence is omitted, not drawn as a zero or a dash, and the
        # remaining columns redistribute across the full content measure.
        self.assertEqual([cell["id"] for cell in rendered["missingCells"]],
                         ["active_days", "efficiency_change", "speed_champion"])
        self.assert_measured_grid(rendered["missingCells"])
        self.assertNotIn("—", [cell["value"] for cell in rendered["missingCells"]])
        self.assertNotIn("EQUIV. SPEND", rendered["missingTexts"])
        self.assertEqual([cell["id"] for cell in rendered["unavailableSpeedCells"]],
                         ["active_days", "efficiency_change", "sessions", "covered_spend"])
        self.assert_measured_grid(rendered["unavailableSpeedCells"])
        # Toggles pick cells in catalog order; unavailable picks drop out.
        self.assertEqual([(cell["label"], cell["value"]) for cell in rendered["pickedCells"]],
                         [("ACTIVE DAYS", "24/30"), ("LONGEST SESSION", "6H 42M")])
        self.assert_measured_grid(rendered["pickedCells"])
        self.assertNotIn("OUTPUT EFFICIENCY", rendered["pickedTexts"])
        self.assertEqual(len(rendered["sixCells"]), 5)
        self.assertNotIn("marathon_session", [cell["id"] for cell in rendered["sixCells"]])
        # A value that cannot fit steps the whole row down to the floor before
        # it ellipsizes; it never overflows its column.
        long_cells = rendered["longCells"]
        self.assertEqual({cell["size"] for cell in long_cells}, {24})
        self.assertTrue(long_cells[3]["value"].endswith("…"))
        self.assertEqual(long_cells[0]["value"], "24/30")
        # Display numerals align by measured ink, not by their advance box.
        self.assertEqual(rendered["inkOps"], [["184,392", 66], ["—", 1011]])
        # Absent commit evidence must read as unavailable, never a measured zero.
        self.assertEqual(rendered["commitOp"][1:4], ["—", 1008, 486])
        self.assertIn("Commits pushed unavailable.", rendered["aria"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for headline verification")
    def test_standalone_headline_band_rhythm_and_controls(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        names = ['safeArray', 'sanitizeBuilderName', 'compactNumber', 'durationLabel', 'formatStat',
                 'formatSpend', 'ellipsize', 'fitText', 'gradientFill', 'radialFill', 'fillRoundedRect',
                 'setTracking', 'cardPalette', 'ditherRegion', 'supportingStats', 'inkX', 'posterStatLimit',
                 'defaultPosterStats', 'posterStatCatalog', 'findRecapStat', 'posterStatCells',
                 'resolveHeadline', 'paperLayout', 'headlineBandHeight', 'statRowHeight',
                 'usageBlockHeight', 'drawHeadlineBand', 'drawStatRow', 'drawUsageGroup',
                 'drawBuilderRecap', 'renderControls', 'resetRecap']
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
for(const name of {json.dumps(names)})eval(extract(name));
class Gradient{{constructor(kind){{this.kind=kind;this.stops=[];}}addColorStop(offset,color){{this.stops.push([offset,color]);}}}}
class Context{{constructor(){{this.ops=[];this.font='';}}fillRect(...v){{this.ops.push(['rect',this.fillStyle,...v]);}}fillText(...v){{this.ops.push(['text',String(v[0]),...v.slice(1),this.font]);}}drawImage(){{}}createLinearGradient(){{return new Gradient('linear');}}createRadialGradient(){{return new Gradient('radial');}}measureText(v){{const size=Number((/([0-9.]+)px/.exec(this.font)||[])[1])||14,narrow=/Narrow|Condensed/.test(this.font);return {{width:String(v).length*size*(narrow?.45:.55)}};}}beginPath(){{}}roundRect(...v){{this.ops.push(['round',...v]);}}fill(){{}}}}
class Canvas{{constructor(){{this.ctx=new Context();this.attrs={{}};}}getContext(){{return this.ctx;}}setAttribute(k,v){{this.attrs[k]=String(v);}}}}
const payload={{range_days:30,current:{{end_day:'2026-09-13'}},spotlights:[{{id:'lines_pushed',available:true,current:184392,unit:'lines'}},{{id:'efficiency_change',available:true,current:2680,unit:'output tokens/$'}},{{id:'speed_champion',available:true,current:42.8,unit:'output tokens/s',leaders:[{{model:'gpt-5.3'}}]}},{{id:'build_streak',available:true,current:14,unit:'days'}}],supporting:[{{id:'sessions',available:true,current:126,unit:'sessions'}},{{id:'covered_spend',available:true,current:2176.88,unit:'USD'}}],
 headlines:[{{id:'identity',title:'A Claude person',value:'92%',detail:'of sessions on Claude Code'}},{{id:'streak',title:'Longest streak',value:'14 days',detail:'consecutive active days'}}],
 activity_days:Array.from({{length:30}},(_,i)=>({{recorded_session:i<24}})),usage:{{agents:[{{label:'Claude Code',share:92}},{{label:'Codex',share:8}}],models:[{{label:'sonnet-4.5',runtime:'Claude Code',share:70}},{{label:'opus-4.1',runtime:'Claude Code',share:20}},{{label:'gpt-5.3',runtime:'Codex',share:10}}]}}}};
function draw(options){{const canvas=new Canvas(),result=drawBuilderRecap(canvas,payload,options);return {{result,texts:canvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]),aria:canvas.attrs['aria-label'],ops:canvas.ctx.ops}};}}
const auto=draw({{name:'',includeUsage:true}}),streak=draw({{name:'',includeUsage:true,headline:'streak'}}),none=draw({{name:'',includeUsage:true,headline:'none'}}),stale=draw({{name:'',includeUsage:true,headline:'best_week'}}),bare=draw({{name:'',includeUsage:false,headline:'none'}});
const band=auto.result.layout.headline,bandRect=auto.ops.find(op=>op[0]==='round'&&op[1]===72&&op[3]===936);
class Control{{constructor(stat){{this.disabled=false;this.checked=false;this.value='';this.textContent='';this.children=[];this.dataset=stat?{{stat}}:{{}};}}appendChild(child){{this.children.push(child);}}}}
const statInputs=posterStatCatalog().map(entry=>new Control(entry.id)),controls={{'builder-name':new Control(),'include-usage':new Control(),'headline-select':new Control()}},$=id=>controls[id];
const document={{querySelectorAll:selector=>selector==='[data-stat]'?statInputs:[],createElement:()=>new Control()}};
const builderRecapState={{range:30,name:'',includeUsage:true,headline:'streak',stats:defaultPosterStats(),payload}};
renderControls(payload);
const select=controls['headline-select'],controlState={{options:select.children.map(option=>[option.value,option.textContent]),value:select.value,stats:statInputs.map(input=>[input.dataset.stat,input.checked,input.disabled])}};
builderRecapState.headline='gone';renderControls(payload);const staleValue=select.value;
builderRecapState.stats=['active_days'];builderRecapState.headline='none';let loads=0;function loadRecap(){{loads++;}}resetRecap();
console.log(JSON.stringify({{auto:{{texts:auto.texts,aria:auto.aria,layout:auto.result.layout}},band,bandRect,streakTexts:streak.texts,noneLayout:none.result.layout,noneTexts:none.texts,staleTexts:stale.texts,bareLayout:bare.result.layout,controlState,staleValue,reset:{{headline:builderRecapState.headline,stats:builderRecapState.stats,loads}}}}));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        # Auto picks the first (highest-priority) candidate and draws it as a band.
        for text in ("92%", "A CLAUDE PERSON", "OF SESSIONS ON CLAUDE CODE"):
            self.assertIn(text, rendered["auto"]["texts"])
        self.assertIn("Headline A Claude person: 92% of sessions on Claude Code.", rendered["auto"]["aria"])
        band = rendered["band"]
        self.assertEqual((band["left"], band["width"]), (72, 936))
        self.assertEqual(rendered["bandRect"][1:5], [72, band["top"], 936, 96])
        self.assertEqual(band["valueY"], band["detailY"])
        self.assertLess(band["titleY"], band["valueY"])
        self.assertGreater(band["textEdge"], band["valueEdge"])
        # One spacing-scale gap separates every paper block, and the stack is
        # centered with equal padding no smaller than that gap.
        layout = rendered["auto"]["layout"]
        blocks = layout["blocks"]
        self.assertIn(layout["gap"], (16, 24, 32, 40, 48))
        order = ["headline", "stats", "rule", "usage"]
        self.assertEqual(sorted(blocks), sorted(order))
        for upper, lower in zip(order, order[1:]):
            self.assertEqual(
                blocks[lower]["top"] - (blocks[upper]["top"] + blocks[upper]["height"]),
                layout["gap"], (upper, lower))
        top_pad = blocks["headline"]["top"] - 720
        bottom_pad = 1220 - (blocks["usage"]["top"] + blocks["usage"]["height"])
        self.assertLessEqual(abs(top_pad - bottom_pad), 1)
        self.assertGreaterEqual(min(top_pad, bottom_pad), layout["gap"])
        self.assertLess(band["top"], layout["stats"][0]["valueY"])
        # Explicit choice, None, and a stale choice that is no longer available.
        self.assertIn("14 days", rendered["streakTexts"])
        self.assertNotIn("A CLAUDE PERSON", rendered["streakTexts"])
        self.assertIsNone(rendered["noneLayout"]["headline"])
        self.assertNotIn("headline", rendered["noneLayout"]["blocks"])
        self.assertNotIn("A CLAUDE PERSON", rendered["noneTexts"])
        self.assertIn("A CLAUDE PERSON", rendered["staleTexts"])
        self.assertEqual(sorted(rendered["bareLayout"]["blocks"]), ["stats"])
        # Controls: Auto, each candidate, None; stat toggles cap at five.
        self.assertEqual(rendered["controlState"]["options"], [
            ["auto", "Auto"], ["identity", "A Claude person · 92%"],
            ["streak", "Longest streak · 14 days"], ["none", "None"],
        ])
        self.assertEqual(rendered["controlState"]["value"], "streak")
        self.assertEqual(rendered["staleValue"], "auto")
        stats = {stat_id: (checked, disabled) for stat_id, checked, disabled in rendered["controlState"]["stats"]}
        for stat_id in ("active_days", "efficiency_change", "speed_champion", "sessions", "covered_spend"):
            self.assertEqual(stats[stat_id], (True, False), stat_id)
        # Five shown: no sixth choice; stats without evidence cannot be picked.
        for stat_id in ("marathon_session", "output_pace", "tool_calls", "models"):
            self.assertEqual(stats[stat_id], (False, True), stat_id)
        self.assertEqual(rendered["reset"], {
            "headline": "auto",
            "stats": ["active_days", "efficiency_change", "speed_champion", "sessions", "covered_spend"],
            "loads": 1,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for headline verification")
    def test_standalone_auto_headline_skips_facts_the_poster_already_shows(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}}}
eval(extract('safeArray'));eval(extract('resolveHeadline'));
const payload={{headlines:[{{id:'identity',title:'A Claude person',value:'92%'}},{{id:'streak',title:'Longest streak',value:'14 days'}},{{id:'cache_share',title:'Cache-powered',value:'87%'}}]}};
const pick=(choice,shown)=>(resolveHeadline(payload,choice,shown)||{{}}).id||null;
console.log(JSON.stringify({{
 fresh:pick('auto',[]),
 skipOne:pick('auto',['identity']),
 skipTwo:pick('auto',['identity','streak']),
 allShown:pick('auto',['identity','streak','cache_share']),
 explicit:pick('identity',['identity']),
 none:pick('none',[])
}}));
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "fresh": "identity", "skipOne": "streak", "skipTwo": "cache_share",
            "allShown": "identity", "explicit": "identity", "none": None,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for texture verification")
    def test_standalone_texture_helpers_dither_gradients_and_degrade_safely(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
for(const name of ['gradientFill','radialFill','fillRoundedRect','ditherRegion'])eval(extract(name));
const put=[];
const readable={{
 getImageData(x,y,width,height){{const data=new Uint8ClampedArray(width*height*4);for(let i=0;i<data.length;i+=4){{data[i]=data[i+1]=data[i+2]=40;data[i+3]=255;}}return {{width,height,data}};}},
 putImageData(image,x,y){{put.push([x,y,Array.from(image.data)]);}},
}};
const dithered=ditherRegion(readable,12,34,16,16,4);
const channels=put[0][2].filter((_,index)=>index%4!==3),alphas=[...new Set(put[0][2].filter((_,index)=>index%4===3))];
const missing=ditherRegion({{}},0,0,4,4,4);
const tainted=ditherRegion({{getImageData(){{throw new Error('tainted');}},putImageData(){{}}}},0,0,4,4,4);
const roundedOps=[],rounded={{roundRect(...v){{roundedOps.push(['roundRect',...v]);}},beginPath(){{roundedOps.push(['beginPath']);}},fill(){{roundedOps.push(['fill']);}},fillRect(...v){{roundedOps.push(['fillRect',...v]);}}}};
const usedRound=fillRoundedRect(rounded,10,20,200,12,6);
const plainOps=[],plain={{fillRect(...v){{plainOps.push(['fillRect',...v]);}}}};
const usedPlain=fillRoundedRect(plain,10,20,200,12,6);
console.log(JSON.stringify({{
 dithered,target:[put[0][0],put[0][1]],levels:[...new Set(channels)].sort((a,b)=>a-b),alphas,
 missing,tainted,usedRound,roundedOps,usedPlain,plainOps,
 gradientFallback:gradientFill({{}},0,0,1,1,[[0,'#111111'],[1,'#222222']]),
 radialFallback:radialFill({{}},0,0,10,[[0,'#333333'],[1,'#444444']]),
}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertTrue(rendered["dithered"])
        self.assertEqual(rendered["target"], [12, 34])
        self.assertEqual(rendered["levels"], [0, 85])
        self.assertEqual(rendered["alphas"], [255])
        self.assertFalse(rendered["missing"])
        self.assertFalse(rendered["tainted"])
        self.assertTrue(rendered["usedRound"])
        self.assertEqual(rendered["roundedOps"], [
            ["beginPath"], ["roundRect", 10, 20, 200, 12, 6], ["fill"],
        ])
        self.assertFalse(rendered["usedPlain"])
        self.assertEqual(rendered["plainOps"], [["fillRect", 10, 20, 200, 12]])
        self.assertEqual(rendered["gradientFallback"], "#222222")
        self.assertEqual(rendered["radialFallback"], "#444444")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for dither wiring verification")
    def test_standalone_canvas_dithers_each_band_before_drawing_any_text(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
for(const name of ['safeArray','sanitizeBuilderName','compactNumber','durationLabel','formatStat','formatSpend','ellipsize','fitText','gradientFill','radialFill','fillRoundedRect','setTracking','cardPalette','ditherRegion','supportingStats','inkX','posterStatLimit','defaultPosterStats','posterStatCatalog','findRecapStat','posterStatCells','resolveHeadline','paperLayout','headlineBandHeight','statRowHeight','usageBlockHeight','drawHeadlineBand','drawStatRow','drawUsageGroup','drawBuilderRecap'])eval(extract(name));
const stops={{addColorStop(){{}}}};
class Context{{
 constructor(){{this.ops=[];this.font='';this.bands=[];}}
 fillRect(...v){{this.ops.push(['rect',...v]);}}
 fillText(v){{this.ops.push(['text',String(v)]);}}
 drawImage(){{this.ops.push(['image']);}}
 createLinearGradient(){{return stops;}}
 createRadialGradient(){{return stops;}}
 getImageData(x,y,width,height){{const data=new Uint8ClampedArray(width*height*4);data.fill(100);return {{width,height,data}};}}
 putImageData(image,x,y){{this.ops.push(['dither',x,y,image.width,image.height]);this.bands.push([x,y,image.width,image.height,[...new Set(image.data.filter((_,i)=>i%4!==3))].sort((a,b)=>a-b)]);}}
 measureText(v){{const size=Number((/([0-9.]+)px/.exec(this.font)||[])[1])||14,narrow=/Narrow|Condensed/.test(this.font);return {{width:String(v).length*size*(narrow?.45:.55)}};}}
 beginPath(){{}}arc(){{}}stroke(){{}}save(){{}}restore(){{}}translate(){{}}rotate(){{}}
}}
class Canvas{{constructor(){{this.ctx=new Context();this.attrs={{}};}}getContext(){{return this.ctx;}}setAttribute(k,v){{this.attrs[k]=String(v);}}}}
const payload={{range_days:30,current:{{end_day:'2026-09-13'}},spotlights:[{{id:'lines_pushed',available:true,current:39425,unit:'lines'}}],supporting:[],activity_days:Array.from({{length:30}},(_,i)=>({{recorded_session:i<26}})),usage:{{agents:[{{label:'Codex',share:71}}],models:[{{label:'gpt-5.6',runtime:'Codex',share:69}}]}}}};
const canvas=new Canvas();drawBuilderRecap(canvas,payload,{{name:'Ada',includeUsage:true}});
const kinds=canvas.ctx.ops.map(op=>op[0]);
console.log(JSON.stringify({{
 bands:canvas.ctx.bands,
 lastDither:kinds.lastIndexOf('dither'),
 firstText:kinds.indexOf('text'),
}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(
            [band[:4] for band in rendered["bands"]],
            [[0, 0, 1080, 720], [0, 720, 1080, 500], [0, 1220, 1080, 130]],
        )
        # Distinct output values pin each band's quantization step, so changing a
        # band's `levels` cannot pass silently.
        self.assertEqual(
            [band[4] for band in rendered["bands"]],
            [[98, 118], [85, 102], [98, 118]],
        )
        self.assertGreater(rendered["firstText"], -1)
        self.assertLess(rendered["lastDither"], rendered["firstText"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for rhythm verification")
    def test_standalone_canvas_groups_long_ranges_into_active_rhythm_bars(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
for(const name of ['safeArray','sanitizeBuilderName','compactNumber','durationLabel','formatStat','formatSpend','ellipsize','fitText','gradientFill','radialFill','fillRoundedRect','setTracking','cardPalette','ditherRegion','supportingStats','inkX','posterStatLimit','defaultPosterStats','posterStatCatalog','findRecapStat','posterStatCells','resolveHeadline','paperLayout','headlineBandHeight','statRowHeight','usageBlockHeight','drawHeadlineBand','drawStatRow','drawUsageGroup','drawBuilderRecap'])eval(extract(name));
class Gradient{{constructor(kind){{this.kind=kind;this.stops=[];}}addColorStop(offset,color){{this.stops.push([offset,color]);}}}}
class Context{{constructor(){{this.ops=[];this.font='';}}fillRect(...v){{this.ops.push(['rect',this.fillStyle,...v]);}}fillText(...v){{this.ops.push(['text',String(v[0]),...v.slice(1),this.font]);}}drawImage(){{}}createLinearGradient(){{return new Gradient('linear');}}createRadialGradient(){{return new Gradient('radial');}}measureText(v){{const size=Number((/([0-9.]+)px/.exec(this.font)||[])[1])||14,narrow=/Narrow|Condensed/.test(this.font);return {{width:String(v).length*size*(narrow?.45:.55)}};}}beginPath(){{}}arc(){{}}stroke(){{}}save(){{}}restore(){{}}translate(){{}}rotate(){{}}}}
class Canvas{{constructor(){{this.ctx=new Context();this.attrs={{}};}}getContext(){{return this.ctx;}}setAttribute(k,v){{this.attrs[k]=String(v);}}}}
const payload={{range_days:90,current:{{end_day:'2026-09-13'}},spotlights:[{{id:'lines_pushed',available:true,current:140514,unit:'lines'}}],supporting:[],activity_days:Array.from({{length:90}},(_,i)=>({{recorded_session:i<76}})),usage:{{agents:[],models:[]}}}};
const canvas=new Canvas();drawBuilderRecap(canvas,payload,{{name:'',includeUsage:true}});
const rhythmBars=canvas.ctx.ops.filter(op=>op[0]==='rect'&&op[2]>=72&&op[2]<1008&&op[3]+op[5]===694);
console.log(JSON.stringify({{
 total:rhythmBars.length,
 active:rhythmBars.filter(op=>op[1]&&op[1].kind==='linear').length,
 inactive:rhythmBars.filter(op=>op[1]==='rgba(159,180,189,.3)').map(op=>op[5]),
 texts:canvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]),
 aria:canvas.attrs['aria-label'],
}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["total"], 30)
        self.assertEqual(rendered["active"], 26)
        self.assertEqual(rendered["inactive"], [8, 8, 8, 8])
        self.assertIn("76/90", rendered["texts"])
        self.assertIn("LAST 90 DAYS", rendered["texts"])
        # Bucketed bars must disclose their grouping instead of implying one day each.
        self.assertIn("Rhythm chart groups 3 days per bar.", rendered["aria"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for canvas verification")
    def test_standalone_canvas_is_a_linkedin_scale_builder_signal(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);if(page.slice(start-6,start)==='async ')start-=6;let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
	for(const name of ['safeArray','sanitizeBuilderName','compactNumber','durationLabel','formatStat','formatSpend','ellipsize','fitText','gradientFill','radialFill','fillRoundedRect','setTracking','cardPalette','ditherRegion','supportingStats','inkX','posterStatLimit','defaultPosterStats','posterStatCatalog','findRecapStat','posterStatCells','resolveHeadline','paperLayout','headlineBandHeight','statRowHeight','usageBlockHeight','drawHeadlineBand','drawStatRow','drawUsageGroup','drawBuilderRecap','hasActivity'])eval(extract(name));
class Gradient{{constructor(kind,coords){{this.kind=kind;this.coords=coords;this.stops=[];}}addColorStop(offset,color){{this.stops.push([offset,color]);}}}}
class Context{{constructor(){{this.ops=[];this.font='';this.lineWidth=1;}}fillRect(...v){{this.ops.push(['rect',this.fillStyle,...v]);}}fillText(...v){{this.ops.push(['text',String(v[0]),...v.slice(1),this.font]);}}createLinearGradient(...c){{return new Gradient('linear',c);}}createRadialGradient(...c){{return new Gradient('radial',c);}}measureText(v){{const size=Number((/([0-9.]+)px/.exec(this.font)||[])[1])||14,narrow=/Narrow|Condensed/.test(this.font);return {{width:String(v).length*size*(narrow?.45:.55)}};}}beginPath(){{}}arc(...v){{this.ops.push(['arc',this.strokeStyle,...v]);}}stroke(){{this.ops.push(['stroke',this.strokeStyle,this.lineWidth]);}}save(){{}}restore(){{}}translate(){{}}rotate(){{}}}}
class Canvas{{constructor(){{this.ctx=new Context();this.attrs={{}};this.width=1;this.height=1;}}getContext(){{return this.ctx;}}setAttribute(k,v){{this.attrs[k]=String(v);}}}}
	const payload={{ok:true,private_sentinel:'prompt://must-not-draw',range_days:30,current:{{start_day:'2026-08-15',end_day:'2026-09-13'}},spotlight_default:'efficiency_change',spotlights:[{{id:'efficiency_change',label:'Output efficiency',family:'efficiency',available:true,current:2680,previous:1942,delta_pct:38,unit:'output tokens/$',basis:'paired cost-covered output and cost'}},{{id:'speed_champion',label:'Fastest model',family:'efficiency',available:true,current:42.8,previous:null,delta_pct:null,unit:'output tokens/s',basis:'weighted measured generation samples',leaders:[{{runtime:'Codex',model:'gpt-5.3'}}]}},{{id:'lines_pushed',label:'Lines pushed',family:'delivery',available:true,current:184392,previous:120000,delta_pct:53.66,unit:'lines',basis:'added plus deleted text lines from successful local pushes'}},{{id:'commits_pushed',label:'Commits pushed',family:'delivery',available:true,current:1284,previous:940,delta_pct:36.6,unit:'commits',basis:'own non-merge commits introduced by successful local pushes'}},{{id:'marathon_session',label:'Longest session',family:'activity',available:true,current:24120,previous:18000,delta_pct:34,unit:'seconds',basis:'active execution duration'}},{{id:'build_streak',label:'Build streak',family:'consistency',available:true,current:14,previous:8,delta_pct:75,unit:'days',basis:'consecutive local days'}}],supporting:[{{id:'active_days',label:'Active days',family:'consistency',available:true,current:24,previous:20,delta_pct:20,unit:'days'}},{{id:'sessions',label:'AI coding sessions',family:'activity',available:true,current:126,previous:91,delta_pct:38.5,unit:'sessions'}},{{id:'delivery_active_days',label:'Delivery-active days',family:'delivery',available:true,current:12,previous:8,delta_pct:50,unit:'days'}},{{id:'output_pace',label:'Output pace',family:'efficiency',available:true,current:31.4,previous:28,delta_pct:12,unit:'output tokens/s'}}],activity_days:Array.from({{length:30}},(_,i)=>({{recorded_session:i<24}})),usage:{{agents:[{{label:'Codex',count:68,share:54}},{{label:'Claude',count:39,share:31}},{{label:'Cursor',count:19,share:15}}],models:[{{label:'gpt-5.3',runtime:'Codex',count:74,share:49}},{{label:'sonnet-4.5',runtime:'Claude',count:48,share:32}},{{label:'gpt-5.2',runtime:'Codex',count:29,share:19}}]}},coverage:{{git:{{available:true}},cost:{{available:true,eligible:22,total:30}},output:{{available:true,eligible:22,total:30}},timing:{{available:true,eligible:14,total:18}}}},privacy:{{content_included:false,project_identity_included:false}}}};
		const canvas=new Canvas(),result=drawBuilderRecap(canvas,payload,{{name:' Ada\\n Builder ',includeUsage:true}}),texts=canvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]),arcs=canvas.ctx.ops.filter(op=>op[0]==='arc'),bars=canvas.ctx.ops.filter(op=>op[0]==='rect'&&(op[5]===12||op[5]===6)),rhythmBars=canvas.ctx.ops.filter(op=>op[0]==='rect'&&op[2]>=72&&op[2]<1008&&op[3]+op[5]===694),heroFields=canvas.ctx.ops.filter(op=>op[0]==='rect'&&op[1]&&op[1].kind==='linear'&&op[2]===0&&op[3]===0&&op[4]===1080&&op[5]>=700),lowerFields=canvas.ctx.ops.filter(op=>op[0]==='rect'&&op[1]&&op[1].kind==='linear'&&op[2]===0&&op[3]>=700&&op[4]===1080&&op[5]>=450),nameOp=canvas.ctx.ops.find(op=>op[0]==='text'&&op[1]==='ADA BUILDER'),heroOp=canvas.ctx.ops.find(op=>op[0]==='text'&&op[3]===486),efficiencyLabelOp=canvas.ctx.ops.find(op=>op[0]==='text'&&op[1]==='OUTPUT EFFICIENCY'&&op[3]===result.layout.stats[0].labelY),metricLabelOps=canvas.ctx.ops.filter(op=>op[0]==='text'&&op[3]===result.layout.stats[0].labelY),ghostPeriodOps=canvas.ctx.ops.filter(op=>op[0]==='text'&&String(op[4]).includes('330px'));
		const negativePayload=JSON.parse(JSON.stringify(payload));negativePayload.spotlights.find(stat=>stat.id==='lines_pushed').delta_pct=-25;const negativeCanvas=new Canvas();drawBuilderRecap(negativeCanvas,negativePayload,{{name:'',includeUsage:true}});
		const unavailablePayload=JSON.parse(JSON.stringify(payload));Object.assign(unavailablePayload.spotlights.find(stat=>stat.id==='lines_pushed'),{{available:false,current:null,previous:null,delta_pct:null}});unavailablePayload.coverage.git.available=false;const unavailableCanvas=new Canvas();drawBuilderRecap(unavailableCanvas,unavailablePayload,{{name:'',includeUsage:true}});
	const flatPayload=JSON.parse(JSON.stringify(payload));flatPayload.spotlights.find(stat=>stat.id==='lines_pushed').delta_pct=0;const flatCanvas=new Canvas();drawBuilderRecap(flatCanvas,flatPayload,{{name:'',includeUsage:true}});const flatTexts=flatCanvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]);
	const privateCanvas=new Canvas();drawBuilderRecap(privateCanvas,payload,{{name:'',includeUsage:false}});const privateTexts=privateCanvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]);
	const noUsagePayload=JSON.parse(JSON.stringify(payload));noUsagePayload.usage={{agents:[],models:[]}};const noUsageCanvas=new Canvas();drawBuilderRecap(noUsageCanvas,noUsagePayload,{{name:'',includeUsage:true}});const noUsageTexts=noUsageCanvas.ctx.ops.filter(op=>op[0]==='text').map(op=>op[1]);
	const gitOnlyPayload=JSON.parse(JSON.stringify(payload));gitOnlyPayload.activity_days=gitOnlyPayload.activity_days.map(day=>({{...day,recorded_session:false}}));const gitOnlyActivity=hasActivity(gitOnlyPayload);
	class Control{{constructor(){{this.disabled=false;this.checked=false;this.value='';this.textContent='';this.children=[];}}appendChild(child){{this.children.push(child);}}}}
	const controlElements={{'builder-name':new Control(),'include-usage':new Control()}},$=id=>controlElements[id],document={{querySelectorAll:()=>[],createElement:()=>new Control()}},builderRecapState={{range:30,name:'',includeUsage:true,payload:noUsagePayload}};function builderOptions(){{return {{name:builderRecapState.name,includeUsage:builderRecapState.includeUsage}};}}eval(extract('renderControls'));renderControls(noUsagePayload);
		const heroRowLabels=canvas.ctx.ops.filter(op=>op[0]==='text'&&op[3]===314).map(op=>[op[1],op[2]]),heroRowValues=canvas.ctx.ops.filter(op=>op[0]==='text'&&op[3]===486).map(op=>[op[1],op[2],op[4]]),columnRules=canvas.ctx.ops.filter(op=>op[0]==='rect'&&op[3]===282&&op[5]===3).map(op=>[op[2],op[4]]);
		console.log(JSON.stringify({{size:[canvas.width,canvas.height],texts,arcs,bars,rhythmBars,heroFields,lowerFields,heroRowLabels,heroRowValues,columnRules,aria:canvas.attrs['aria-label'],negativeAria:negativeCanvas.attrs['aria-label'],unavailableAria:unavailableCanvas.attrs['aria-label'],flatAria:flatCanvas.attrs['aria-label'],flatTexts,nameOp,heroOp,efficiencyLabelOp,metricLabelOps,ghostPeriodOps,summary:result.summary,privateTexts,privateAria:privateCanvas.attrs['aria-label'],noUsageTexts,noUsageAria:noUsageCanvas.attrs['aria-label'],gitOnlyActivity,noUsageControl:{{disabled:controlElements['include-usage'].disabled,checked:controlElements['include-usage'].checked}}}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["size"], [1080, 1350])
        for text in (
            "TOKEN METER", "PERFORMANCE CARD", "LAST 30 DAYS", "30D",
            "ADA BUILDER", "LINES PUSHED",
            "184,392", "OUTPUT EFFICIENCY", "2.68 K/$", "24/30",
            "ACTIVE DAYS", "14 DAY STREAK", "FASTEST MODEL", "42.8 TOK/S",
            "GPT-5.3", "AI SESSIONS", "126",
            "MOST-USED AGENT", "GO-TO MODEL", "CODEX", "54%", "49%",
            "BUILD YOURS →", "github.com/splunk/token-meter",
        ):
            self.assertIn(text, rendered["texts"])
        self.assertEqual(rendered["nameOp"][1:4], ["ADA BUILDER", 72, 250])
        self.assertIn("44px", rendered["nameOp"][4])
        self.assertEqual(rendered["heroOp"][1], "184,392")
        # Lines and commits form one two-column hero: labels share a line, values
        # share a baseline, and each column carries its own accent rule.
        self.assertEqual(
            [(op[0], op[1]) for op in rendered["heroRowLabels"]],
            [("LINES PUSHED", 72), ("COMMITS", 1008)],
        )
        self.assertEqual(
            [(op[0], op[1]) for op in rendered["heroRowValues"]],
            [("184,392", 72), ("1,284", 1008)],
        )
        self.assertEqual(rendered["columnRules"], [[72, 108], [900, 108]])
        # Commits must stay visually subordinate to the lines hero.
        hero_size = int(re.search(r"(\d+)px", rendered["heroRowValues"][0][2]).group(1))
        commit_size = int(re.search(r"(\d+)px", rendered["heroRowValues"][1][2]).group(1))
        self.assertLess(commit_size, hero_size)
        self.assertIn("Commits pushed 1,284.", rendered["aria"])
        self.assertIsNotNone(rendered["efficiencyLabelOp"])
        # No covered-spend evidence: the spend cell is omitted and four equal
        # columns span the content measure instead of leaving a gap.
        self.assertEqual(
            [(op[1], op[2]) for op in rendered["metricLabelOps"]],
            [("ACTIVE DAYS", 72), ("OUTPUT EFFICIENCY", 314),
             ("FASTEST MODEL", 556), ("AI SESSIONS", 798)],
        )
        self.assertNotIn("EQUIV. SPEND", rendered["texts"])
        self.assertEqual(rendered["ghostPeriodOps"], [])
        self.assertGreaterEqual(len(rendered["bars"]), 6)
        self.assertEqual(len(rendered["rhythmBars"]), 30)
        for index, bar in enumerate(rendered["rhythmBars"][:24]):
            self.assertEqual(bar[1]["kind"], "linear")
            self.assertEqual(
                [stop[1] for stop in bar[1]["stops"]],
                ["#8ee9f2", "#00bceb", "rgba(64,84,214,.72)"],
            )
            self.assertEqual(bar[5], 28 + ((index * 19) % 66))
        for bar in rendered["rhythmBars"][24:]:
            self.assertEqual((bar[1], bar[5]), ("rgba(159,180,189,.3)", 8))
        self.assertTrue(rendered["heroFields"])
        self.assertTrue(rendered["lowerFields"])
        self.assertEqual(
            [stop[1] for stop in rendered["heroFields"][0][1]["stops"]],
            ["#0d2c3d", "#081a27", "#040d14"],
        )
        self.assertEqual(
            [stop[1] for stop in rendered["lowerFields"][0][1]["stops"]],
            ["#f8f5ee", "#f1eee4", "#e4e0d3"],
        )
        self.assertFalse(rendered["arcs"])
        for raw_count in ("68", "39", "19", "74", "48", "29"):
            self.assertNotIn(raw_count, rendered["texts"])
        visible = " ".join(rendered["texts"])
        for rejected in (
            "BUILDER FIELD SHEET", "DAY SIGNAL", "30-DAY RHYTHM", "AGENT MIX",
            "MODEL MIX", "LOCAL / PARTIAL",
            "PRIVATE LOCAL AGGREGATES", "AI BUILDER RECAP", "prompt://must-not-draw",
            "VS PRIOR", "↑", "↓", "+54%", "−25%", "NO CHANGE",
            "LONGEST SESSION", "6H 42M",
        ):
            self.assertNotIn(rejected, visible)
        self.assertIn(
            "Name Ada Builder. Current lines pushed 184,392 lines.",
            rendered["aria"],
        )
        self.assertIn(
            "Current lines pushed 184,392 lines.",
            rendered["negativeAria"],
        )
        self.assertIn(
            "Current lines pushed unavailable.",
            rendered["unavailableAria"],
        )
        self.assertIn(
            "Current lines pushed 184,392 lines.", rendered["flatAria"],
        )
        for output in (
            rendered["aria"], rendered["negativeAria"],
            rendered["unavailableAria"], rendered["flatAria"],
            rendered["summary"], " ".join(rendered["flatTexts"]),
        ):
            for rejected in (
                "Partial evidence", "Complete evidence", "increased", "decreased",
                "unchanged", "versus prior", "comparison", "VS PRIOR", "↑", "↓",
            ):
                self.assertNotIn(rejected, output)
        self.assertTrue(rendered["gitOnlyActivity"])
        self.assertIn("runtime-scoped model usage", rendered["aria"])
        self.assertNotIn("prompt://must-not-draw", rendered["aria"])
        private_visible = " ".join(rendered["privateTexts"])
        self.assertIn("LINES PUSHED", private_visible)
        self.assertIn("184,392", rendered["privateTexts"])
        self.assertIn("OUTPUT EFFICIENCY", private_visible)
        for excluded in ("MOST-USED AGENT", "GO-TO MODEL", "CODEX", "CLAUDE",
                         "CURSOR", "GPT-5.3", "SONNET-4.5", "GPT-5.2"):
            self.assertNotIn(excluded, private_visible)
        for excluded in ("Codex", "Claude", "Cursor", "gpt-5.3", "sonnet-4.5", "gpt-5.2"):
            self.assertNotIn(excluded, rendered["privateAria"])
        self.assertIn("Usage charts excluded", rendered["privateAria"])
        no_usage_visible = " ".join(rendered["noUsageTexts"])
        self.assertNotIn("MOST-USED AGENT", no_usage_visible)
        self.assertNotIn("GO-TO MODEL", no_usage_visible)
        self.assertIn("Usage charts unavailable", rendered["noUsageAria"])
        self.assertEqual(rendered["noUsageControl"], {
            "disabled": True, "checked": False,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for preview verification")
    def test_standalone_success_enables_download_without_export_ready_copy(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
class Element{{constructor(){{this.disabled=true;this.hidden=true;this.textContent='';this.dataset={{}};}}}}
const elements={{download:new Element(),retry:new Element(),poster:new Element(),status:new Element()}},$=id=>elements[id];
elements.poster.getContext=()=>({{}});elements.poster.setAttribute=()=>{{}};
const builderRecapState={{payload:{{activity_days:[{{recorded_session:true}}]}},loading:false,error:'',rendered:false}};
const logo={{complete:false,naturalWidth:0}};let shouldFail=false;function hasActivity(){{return true;}}function drawBuilderRecap(){{if(shouldFail)throw Error('private failure');}}function drawPlaceholder(){{}}function builderOptions(){{return {{logo}};}}function setStatus(message,kind=''){{elements.status.textContent=message;elements.status.dataset.kind=kind;}}
for(const name of ['showLogoFailure','renderPreview'])eval(extract(name));
const waitingReady=renderPreview(),waiting={{ready:waitingReady,download:elements.download.disabled,status:elements.status.textContent}};
logo.complete=true;logo.naturalWidth=1024;
const ready=renderPreview(),success={{ready,download:elements.download.disabled,status:elements.status.textContent,kind:elements.status.dataset.kind}};
shouldFail=true;const failed=renderPreview(),failure={{failed,download:elements.download.disabled,retry:elements.retry.hidden,status:elements.status.textContent,kind:elements.status.dataset.kind}};
console.log(JSON.stringify({{waiting,success,failure}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["waiting"], {
            "ready": False, "download": True,
            "status": "Loading local brand asset…",
        })
        self.assertEqual(rendered["success"], {
            "ready": True, "download": False, "status": "", "kind": "",
        })
        self.assertEqual(rendered["failure"], {
            "failed": False, "download": True, "retry": False,
            "status": "Preview unavailable. Try again.", "kind": "error",
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for logo recovery verification")
    def test_standalone_logo_error_retries_local_asset_before_enabling_download(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)return '';let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
class Element{{
 constructor(){{this.disabled=true;this.hidden=true;this.textContent='';this.dataset={{}};this.listeners={{}};}}
 addEventListener(type,handler){{this.listeners[type]=handler;}}
 dispatch(type){{if(this.listeners[type])this.listeners[type]({{target:this}});}}
}}
class Logo extends Element{{
 constructor(){{super();this.complete=false;this.naturalWidth=0;this._src='/assets/brand/logo-splunk-by-cisco-rgb-w.png';this.sourceWrites=[];this.sourceRemovals=0;}}
 get src(){{return this._src;}}
 set src(value){{this._src=String(value);this.sourceWrites.push(this._src);}}
 getAttribute(name){{return name==='src'?this._src:null;}}
 removeAttribute(name){{if(name==='src'){{this._src='';this.sourceRemovals++;}}}}
}}
const elements={{download:new Element(),retry:new Element(),reset:new Element(),poster:new Element(),status:new Element(),'performance-card-logo':new Logo()}},$=id=>elements[id];
elements.poster.getContext=()=>({{}});elements.poster.setAttribute=()=>{{}};
const builderRecapState={{range:30,payload:{{activity_days:[{{recorded_session:true}}]}},loading:false,error:'',rendered:false,logoError:false}};
let recapLoads=0;function loadRecap(){{recapLoads++;}}function resetRecap(){{}}function downloadRecap(){{}}function hasActivity(){{return true;}}function drawBuilderRecap(){{}}function drawPlaceholder(){{}}function builderOptions(){{return {{logo:elements['performance-card-logo']}};}}function setStatus(message,kind=''){{elements.status.textContent=message;elements.status.dataset.kind=kind;}}
for(const name of ['showLogoFailure','retryLogo','handleLogoLoad','renderPreview']){{const source=extract(name);if(source)eval(source);}}
for(const line of page.split('\\n').filter(line=>line.includes("$('retry').addEventListener")||line.includes("$('performance-card-logo').addEventListener")))eval(line);
renderPreview();
const logo=elements['performance-card-logo'];logo.complete=true;logo.dispatch('error');
const failure={{download:elements.download.disabled,retry:elements.retry.hidden,status:elements.status.textContent,kind:elements.status.dataset.kind}};
elements.retry.dispatch('click');
const retrying={{download:elements.download.disabled,retry:elements.retry.hidden,status:elements.status.textContent,kind:elements.status.dataset.kind,source:logo.src,sourceWrites:logo.sourceWrites,sourceRemovals:logo.sourceRemovals,recapLoads}};
logo.complete=true;logo.naturalWidth=1024;logo.dispatch('load');
const recovered={{rendered:builderRecapState.rendered,download:elements.download.disabled,retry:elements.retry.hidden,status:elements.status.textContent,kind:elements.status.dataset.kind}};
console.log(JSON.stringify({{failure,retrying,recovered}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["failure"], {
            "download": True, "retry": False,
            "status": "Local brand asset unavailable. Try again.",
            "kind": "error",
        })
        self.assertEqual(rendered["retrying"], {
            "download": True, "retry": True,
            "status": "Loading local brand asset…", "kind": "",
            "source": "/assets/brand/logo-splunk-by-cisco-rgb-w.png",
            "sourceWrites": ["/assets/brand/logo-splunk-by-cisco-rgb-w.png"],
            "sourceRemovals": 1, "recapLoads": 0,
        })
        self.assertEqual(rendered["recovered"], {
            "rendered": True, "download": False, "retry": True,
            "status": "", "kind": "",
        })

    def test_standalone_export_contract_is_png_only(self):
        page = Path(meter.__file__).with_name("performance.html").read_text()
        for marker in (
            "canvas.width=1080", "canvas.height=1350", "canvas.toBlob(blob=>",
            "'image/png'", "URL.revokeObjectURL", "function downloadRecap(",
            "function resetRecap(", "new AbortController()",
        ):
            self.assertIn(marker, page)
        self.assertNotIn("LOCAL / PARTIAL", page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for input verification")
    def test_standalone_name_input_preserves_spaces_while_typing(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
const input={{value:'',addEventListener(type,handler){{this.handler=handler;}}}},$=id=>input;
const builderRecapState={{name:''}};let renders=0;function renderPreview(){{renders++;}}
eval(extract('sanitizeBuilderName'));
const handlerSource=page.split('\\n').find(line=>line.startsWith("$('builder-name').addEventListener('input',"));eval(handlerSource);
for(const character of 'Ada Lovelace'){{input.value+=character;input.handler({{target:input}});}}
console.log(JSON.stringify({{field:input.value,state:builderRecapState.name,renders}}));
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "field": "Ada Lovelace", "state": "Ada Lovelace", "renders": 12,
        })

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for request verification")
    def test_standalone_request_lifecycle_is_latest_only_and_retryable(self):
        page_path = Path(meter.__file__).with_name("performance.html")
        script = f"""
const fs=require('fs'),page=fs.readFileSync({json.dumps(str(page_path))},'utf8');
function extract(name){{let start=page.indexOf(`function ${{name}}(`);if(start<0)throw Error(`missing ${{name}}`);if(page.slice(start-6,start)==='async ')start-=6;let i=page.indexOf('{{',start),depth=0;for(;i<page.length;i++){{if(page[i]==='{{')depth++;else if(page[i]==='}}'&&--depth===0)return page.slice(start,i+1);}}throw Error(`unclosed ${{name}}`);}}
class Element{{constructor(){{this.disabled=false;this.hidden=true;this.checked=false;this.value='';this.dataset={{}};}}}}
const elements=Object.fromEntries(['download','retry','builder-name','include-usage'].map(id=>[id,new Element()])), $=id=>elements[id];
const builderRecapState={{range:30,name:'',includeUsage:true,payload:null,request:0,controller:null,loading:false,error:'',rendered:false}};
class AbortController{{constructor(){{this.signal={{aborted:false}};}}abort(){{this.signal.aborted=true;}}}}
let requests=[],status='';function fetch(url,options){{return new Promise(resolve=>requests.push({{url,options,resolve}}));}}function renderControls(){{}}function drawPlaceholder(){{}}function setStatus(message){{status=message;}}function renderPreview(){{builderRecapState.rendered=true;$('download').disabled=false;return true;}}
eval(extract('sanitizeBuilderName'));eval(extract('defaultPosterStats'));eval(extract('loadRecap'));eval(extract('resetRecap'));
const payload=range=>({{ok:true,range_days:range,activity_days:[{{recorded_session:true}}],coverage:{{git:{{available:true}}}}}});
;(async()=>{{
 const first=loadRecap(30);await Promise.resolve();const second=loadRecap(7);await Promise.resolve();const third=loadRecap(90);await Promise.resolve();
 requests[0].resolve({{ok:true,json:async()=>payload(30)}});requests[1].resolve({{ok:true,json:async()=>payload(7)}});requests[2].resolve({{ok:true,json:async()=>payload(90)}});await Promise.all([first,second,third]);
 const latest={{range:builderRecapState.range,payload:builderRecapState.payload.range_days,aborted:requests[1].options.signal.aborted,download:$('download').disabled}};
 const sanitized=sanitizeBuilderName('  Ada\\n\\u0000 Lovelace  ');
 const failedPromise=loadRecap(7);await Promise.resolve();requests[3].resolve({{ok:false,json:async()=>({{}})}});await failedPromise;const failed={{download:$('download').disabled,retry:$('retry').hidden,status}};
 builderRecapState.name='Ada';builderRecapState.includeUsage=false;const resetPromise=resetRecap();await Promise.resolve();requests[4].resolve({{ok:true,json:async()=>payload(30)}});await resetPromise;
 const reset={{range:builderRecapState.range,name:builderRecapState.name,usage:builderRecapState.includeUsage}};
 console.log(JSON.stringify({{latest,sanitized,failed,reset,urls:requests.map(item=>item.url)}}));
}})().catch(error=>{{console.error(error.stack||error);process.exitCode=1;}});
"""
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["latest"], {
            "range": 90, "payload": 90, "aborted": True, "download": False,
        })
        self.assertEqual(rendered["sanitized"], "Ada Lovelace")
        self.assertEqual(rendered["failed"], {
            "download": True, "retry": False,
            "status": "Unable to load the builder recap. Try again.",
        })
        self.assertEqual(rendered["reset"], {
            "range": 30, "name": "", "usage": True,
        })
        self.assertEqual(rendered["urls"], [
            "/builder-recap?range=30", "/builder-recap?range=7",
            "/builder-recap?range=90", "/builder-recap?range=7",
            "/builder-recap?range=30",
        ])


if __name__ == "__main__":
    unittest.main()
