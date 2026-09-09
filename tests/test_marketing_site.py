import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs"
PAGES_WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


class _SiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = set()
        self.links = []
        self.images = []
        self.scripts = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.append((tag, values))
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a":
            self.links.append(values.get("href", ""))
        if tag == "img":
            self.images.append(values)
        if tag == "script":
            self.scripts.append(values.get("src", ""))
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href", ""))


class MarketingSiteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (SITE / "index.html").read_text(encoding="utf-8")
        cls.css = (SITE / "styles.css").read_text(encoding="utf-8")
        cls.js = (SITE / "script.js").read_text(encoding="utf-8")
        cls.parser = _SiteParser()
        cls.parser.feed(cls.html)

    def test_static_site_has_complete_local_entrypoints(self):
        self.assertTrue((SITE / "index.html").is_file())
        self.assertTrue((SITE / "styles.css").is_file())
        self.assertTrue((SITE / "script.js").is_file())
        self.assertTrue((SITE / "favicon.svg").is_file())
        self.assertEqual(self.parser.stylesheets, ["styles.css"])
        self.assertEqual(self.parser.scripts, ["tab-controller.js", "script.js"])
        self.assertNotRegex(self.html, r"<(?:script|link)[^>]+https?://")
        self.assertNotIn("@import", self.css)

    def test_page_has_semantic_navigation_and_one_primary_heading(self):
        self.assertEqual(len(re.findall(r"<h1\b", self.html)), 1)
        for landmark in ("<header", "<nav", "<main", "<footer"):
            self.assertIn(landmark, self.html)
        for section_id in (
            "product", "capabilities", "efficiency", "privacy", "install",
        ):
            self.assertIn(section_id, self.parser.ids)
            self.assertIn("#" + section_id, self.parser.links)
        self.assertIn('class="skip-link"', self.html)
        self.assertIn('href="#main-content"', self.html)

    def test_copy_keeps_product_claims_bounded_and_installable(self):
        for claim in (
            "Local-first observability for AI coding agents",
            "No API keys for trace analysis",
            "No Token Meter analytics or telemetry leaves your machine",
            "Costs and selected token values can be estimates",
            "Windows extension is in beta",
            "http://127.0.0.1:8722",
            "git clone https://github.com/splunk/token-meter.git",
            "./token-meter/scripts/install",
        ):
            self.assertIn(claim, self.html)
        self.assertNotIn("100% private", self.html)
        self.assertNotIn("zero data", self.html.lower())

    def test_hero_leads_with_optimisation_and_one_tabbed_install_frame(self):
        self.assertIn('<h1 id="hero-title">', self.html)
        self.assertIn("Optimise your coding agents.", re.sub(r"<[^>]+>", "", self.html))
        hero_end = self.html.index('<section class="proof-section"')
        hero_copy = self.html[:hero_end]
        self.assertIn(
            'class="install-card hero-install-card" id="install"',
            hero_copy,
        )
        for platform in ("macos", "linux", "windows"):
            self.assertIn(f'id="tab-{platform}"', hero_copy)
            self.assertIn(f'id="panel-{platform}"', hero_copy)
        self.assertIn(
            "git clone https://github.com/splunk/token-meter.git",
            hero_copy,
        )
        self.assertIn("./token-meter/scripts/install", hero_copy)
        self.assertIn("powershell.exe", hero_copy)
        for prerequisite in (
            "Python 3.8+ · Xcode Command Line Tools · curl",
            "Python 3.8+ · systemd user services · GTK 3 · PyGObject",
            "Windows PowerShell · WinGet",
        ):
            self.assertIn(prerequisite, hero_copy)
        self.assertEqual(self.html.count('id="install"'), 1)
        self.assertEqual(self.html.count('id="copy-install"'), 1)
        self.assertEqual(self.html.count('class="install-card '), 1)
        self.assertNotIn('class="section install-section"', self.html)

    def test_hero_typography_is_structured_for_desktop_readability(self):
        self.assertIn('<span class="hero-title-line">Optimise your</span>', self.html)
        self.assertIn(
            '<span class="hero-title-line hero-title-emphasis">coding agents.</span>',
            self.html,
        )
        for rule in (
            "--label-size: 11px;",
            "--micro-size: 10px;",
            "font-size: clamp(4.6rem, 7.3vw, 8.4rem);",
            "line-height: 0.86;",
            ".hero-title-emphasis",
        ):
            self.assertIn(rule, self.css)
        self.assertNotIn("font-size: clamp(5rem, 10vw, 10.8rem);", self.css)

    def test_product_proof_uses_approved_local_screenshots(self):
        expected = {
            "images/dashboard.png",
            "images/spend.png",
            "images/tool-analytics.png",
        }
        product_images = [
            image for image in self.parser.images
            if image.get("src") in expected
        ]
        sources = {image.get("src") for image in product_images}
        self.assertTrue(expected.issubset(sources))
        for image in product_images:
            self.assertTrue(image.get("alt"), image)
            self.assertEqual(image.get("loading"), "lazy")
        for relative in expected:
            self.assertTrue((ROOT / relative).is_file())

    def test_native_companion_is_semantic_web_ui_not_a_snapshot(self):
        self.assertNotIn("menu-bar-widget.png", self.html)
        self.assertFalse((SITE / "images" / "menu-bar-widget.png").exists())
        self.assertIn('class="native-menu-demo"', self.html)
        self.assertIn('class="native-statusbar"', self.html)
        self.assertIn('class="native-popover"', self.html)
        for label in (
            "Open Dashboard", "Recent sessions", "Follow Latest",
            "Completed #16", "Summarize soon", "Cost", "Tokens",
            "Cache", "Context", "Last execution", "Watch closely",
        ):
            self.assertIn(label, self.html)

    def test_efficiency_has_a_dedicated_evidence_qualified_section(self):
        self.assertIn('class="efficiency-section" id="efficiency"', self.html)
        self.assertIn("Efficiency, with the evidence beside it.", self.html)
        for signal in (
            "Output / covered $", "Reasoning ratio", "Context load",
            "Output / execution",
        ):
            self.assertIn(signal, self.html)
        for qualifier in (
            "No universal score", "comparable work", "Coverage remains visible",
            "Costs and selected token values can be estimates",
        ):
            self.assertIn(qualifier, self.html)
        self.assertNotIn("Efficiency score", self.html)

    def test_splunk_wordmarks_use_the_local_brand_image(self):
        logo_source = "images/logo-splunk-acc-rgb-w.png"
        logo_images = [
            image for image in self.parser.images
            if image.get("src") == logo_source
        ]
        self.assertEqual(len(logo_images), 3)
        for image in logo_images:
            self.assertEqual(image.get("class"), "splunk-logo")
            self.assertEqual(image.get("alt"), "Splunk")
        self.assertEqual(
            [(image.get("width"), image.get("height")) for image in logo_images],
            [("25", "10"), ("76", "30"), ("23", "9")],
        )
        self.assertTrue((SITE / logo_source).is_file())
        self.assertIn(".splunk-logo", self.css)
        self.assertRegex(
            self.css,
            r"\.brand-by \{[^}]*align-items: center;",
        )
        self.assertIn(
            ".brand-by .splunk-logo {\n  height: 12px;\n}",
            self.css,
        )
        self.assertNotIn("splunk&gt;", self.html)
        self.assertNotRegex(self.html, r"https?://[^\"']*(?:splunk|logo)[^\"']*\.(?:svg|png)")

    def test_signal_print_rejects_generic_saas_composition(self):
        self.assertNotIn("One instrument for the work", self.html)
        self.assertIn("Every run leaves a signal.", self.html)
        self.assertIn("Read the cost. See the work.", self.html)
        self.assertIn('class="proof-viewer"', self.html)
        self.assertIn('class="signal-ledger"', self.html)
        for mode in ("plume", "scan", "orbit"):
            self.assertIn(f'data-dither="{mode}"', self.html)
            self.assertIn(f"case '{mode}'", self.js)
        self.assertEqual(self.html.count('class="dither-canvas"'), 3)
        self.assertIn("canvas.dataset.dither", self.js)
        for rejected in (
            "hero-instrument", "instrument-window", "bento-grid",
            "runtime-orbit", "local-diagram",
        ):
            self.assertNotIn(rejected, self.html)
        self.assertNotIn(".bento", self.css)
        self.assertNotIn("backdrop-filter", self.css)

    def test_privacy_dither_is_a_closed_boundary_not_a_letterform(self):
        self.assertIn("const closedRing =", self.js)
        self.assertIn("const boundaryEcho =", self.js)
        self.assertNotIn("const gate = nx > 0.48", self.js)
        self.assertIn("closedRing * 1.08 + boundaryEcho", self.js)

    def test_interactions_are_keyboard_and_reduced_motion_safe(self):
        self.assertEqual(self.html.count('aria-hidden="true"></canvas>'), 3)
        self.assertIn('aria-hidden="true"', self.html)
        self.assertIn('id="copy-install"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('role="tablist"', self.html)
        self.assertIn('role="tab"', self.html)
        self.assertIn("prefers-reduced-motion: reduce", self.css)
        self.assertIn("matchMedia('(prefers-reduced-motion: reduce)')", self.js)
        self.assertIn(":focus-visible", self.css)
        self.assertNotRegex(self.html, r"\son[a-z]+=\"")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for site JavaScript")
    def test_site_javascript_parses(self):
        for script in ("tab-controller.js", "script.js"):
            subprocess.run(
                ["node", "--check", str(SITE / script)],
                check=True,
                capture_output=True,
                text=True,
            )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for tab behavior")
    def test_tab_controller_selects_panels_and_wraps_keyboard_navigation(self):
        driver = r"""
const tabsApi = require('./docs/tab-controller.js');
const panels = { first: { hidden: false }, second: { hidden: true } };
const makeTab = target => ({
  dataset: { tabTarget: target }, tabIndex: 0, attrs: {},
  setAttribute(name, value) { this.attrs[name] = value; },
});
const first = makeTab('first');
const second = makeTab('second');
tabsApi.selectTab([first, second], second, id => panels[id]);
console.log(JSON.stringify({
  firstSelected: first.attrs['aria-selected'],
  secondSelected: second.attrs['aria-selected'],
  firstHidden: panels.first.hidden,
  secondHidden: panels.second.hidden,
  next: tabsApi.nextTabIndex('ArrowRight', 1, 2),
  previous: tabsApi.nextTabIndex('ArrowLeft', 0, 2),
  home: tabsApi.nextTabIndex('Home', 1, 2),
  end: tabsApi.nextTabIndex('End', 0, 2),
  ignored: tabsApi.nextTabIndex('Enter', 0, 2),
}));
"""
        result = subprocess.run(
            ["node", "-e", driver], cwd=ROOT, check=True,
            capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.strip(), (
            '{"firstSelected":"false","secondSelected":"true",'
            '"firstHidden":true,"secondHidden":false,"next":0,'
            '"previous":1,"home":0,"end":1,"ignored":null}'
        ))


class GitHubPagesBranchSourceContractTests(unittest.TestCase):
    def test_pages_source_is_self_contained_under_docs(self):
        self.assertFalse(PAGES_WORKFLOW.exists())
        self.assertFalse((ROOT / "site").exists())
        self.assertTrue((SITE / "index.html").is_file())
        self.assertTrue((SITE / ".nojekyll").is_file())

    def test_exact_branch_source_contains_every_local_reference(self):
        approved_images = (
            "dashboard.png", "logo-splunk-acc-rgb-w.png", "spend.png",
            "tool-analytics.png",
        )
        deployed = _SiteParser()
        deployed.feed((SITE / "index.html").read_text(encoding="utf-8"))
        local_references = set(deployed.stylesheets + deployed.scripts)
        local_references.update(
            image["src"] for image in deployed.images
            if not image["src"].startswith(("http://", "https://"))
        )
        local_references.add("favicon.svg")
        self.assertEqual(
            sorted(local_references),
            sorted(
                {"styles.css", "tab-controller.js", "script.js", "favicon.svg"}
                | {"images/" + image for image in approved_images}
            ),
        )
        for reference in local_references:
            self.assertTrue((SITE / reference).is_file(), reference)

        self.assertEqual(
            sorted(
                str(path.relative_to(SITE))
                for path in SITE.rglob("*") if path.is_file()
            ),
            sorted(
                [".nojekyll", "index.html", "styles.css", "tab-controller.js",
                 "script.js", "favicon.svg"]
                + ["images/" + image for image in approved_images]
            ),
        )


if __name__ == "__main__":
    unittest.main()
