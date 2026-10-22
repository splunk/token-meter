import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs"


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
        cls.robots = (SITE / "robots.txt").read_text(encoding="utf-8")
        cls.sitemap = (SITE / "sitemap.xml").read_text(encoding="utf-8")
        cls.llms = (SITE / "llms.txt").read_text(encoding="utf-8")
        cls.parser = _SiteParser()
        cls.parser.feed(cls.html)

    def test_site_has_search_and_agent_discovery_metadata(self):
        self.assertIn(
            "<title>Token Meter — Local AI coding agent usage and cost dashboard</title>",
            self.html,
        )
        for metadata in (
            'name="description" content="Open-source, local-first dashboard for AI coding agent token usage, estimated cost, context, time, tools, and execution across Claude, Codex, Cursor, and more."',
            'property="og:title" content="Token Meter — AI coding agent usage and cost, locally"',
            'rel="canonical" href="https://splunk.github.io/token-meter/"',
            'rel="describedby" href="llms.txt"',
            'property="og:url" content="https://splunk.github.io/token-meter/"',
            'property="og:image" content="https://splunk.github.io/token-meter/images/dashboard.png"',
            'name="twitter:image" content="https://splunk.github.io/token-meter/images/dashboard.png"',
        ):
            self.assertIn(metadata, self.html)

        self.assertEqual(
            self.robots,
            "User-agent: *\nAllow: /\n\n"
            "Sitemap: https://splunk.github.io/token-meter/sitemap.xml\n",
        )
        sitemap_root = ET.fromstring(self.sitemap)
        locations = sitemap_root.findall(
            ".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
        )
        self.assertEqual(
            [location.text for location in locations],
            [
                "https://splunk.github.io/token-meter/",
                "https://splunk.github.io/token-meter/token-optimization.html",
                "https://splunk.github.io/token-meter/talk/",
            ],
        )

    def test_llms_txt_is_concise_factual_and_points_to_canonical_docs(self):
        self.assertTrue(self.llms.startswith("# Token Meter\n\n> "))
        for statement in (
            "open-source, local-first usage and cost dashboard",
            "Costs and selected token values can be estimates",
            "missing values remain unavailable rather than becoming zero",
            "No Token Meter analytics or telemetry leaves the user's machine",
        ):
            self.assertIn(statement, self.llms)
        for path in (
            "README.md", "specs/USER_GUIDE.md", "specs/SECURITY.md",
            "specs/ARCHITECTURE.md", "specs/CONTRIBUTING.md",
        ):
            self.assertIn(
                f"https://raw.githubusercontent.com/splunk/token-meter/main/{path}",
                self.llms,
            )
        self.assertNotIn("best", self.llms.lower())
        self.assertNotIn("leading", self.llms.lower())

    def test_page_has_semantic_navigation_and_one_primary_heading(self):
        self.assertEqual(len(re.findall(r"<h1\b", self.html)), 1)
        for landmark in ("<header", "<nav", "<main", "<footer"):
            self.assertIn(landmark, self.html)
        for section_id in (
            "product", "capabilities", "efficiency", "privacy", "install",
        ):
            self.assertIn(section_id, self.parser.ids)
            self.assertIn("#" + section_id, self.parser.links)
        for href in self.parser.links:
            if href.startswith("#"):
                self.assertIn(href[1:], self.parser.ids, href)
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
        heading = re.search(r'<h1 id="hero-title">(.*?)</h1>', self.html, re.S)
        self.assertIsNotNone(heading)
        self.assertEqual(
            re.findall(r"<span[^>]*>([^<]+)</span>", heading.group(1)),
            ["Optimise your", "coding agents."],
        )
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

    def test_native_companion_is_semantic_web_ui_not_a_snapshot(self):
        self.assertNotIn("menu-bar-widget.png", self.html)
        self.assertFalse((SITE / "images" / "menu-bar-widget.png").exists())
        native = self.html.split('id="native-companion"', 1)[1].split("</figure>", 1)[0]
        self.assertIn('role="img"', native)
        self.assertIn('aria-label="Web recreation of the Token Meter macOS menu-bar companion', native)
        for label in ("Cost", "Tokens", "Cache", "Context", "Last execution"):
            self.assertIn(label, native)

    def test_efficiency_has_a_dedicated_evidence_qualified_section(self):
        match = re.search(r'<section[^>]+id="efficiency".*?</section>', self.html, re.S)
        self.assertIsNotNone(match)
        efficiency = match.group(0)
        self.assertIn("Efficiency, with the evidence beside it.", efficiency)
        for signal in (
            "Output / covered $", "Reasoning ratio", "Context load",
            "Cache hit ratio",
        ):
            self.assertIn(signal, efficiency)
        for qualifier in (
            "No universal score", "comparable work", "Coverage remains visible",
            "Costs and selected token values can be estimates",
        ):
            self.assertIn(qualifier, efficiency)
        self.assertNotIn("Efficiency score", efficiency)

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
            [("50", "20"), ("76", "30"), ("23", "9")],
        )
        self.assertTrue((SITE / logo_source).is_file())
        self.assertIn(".splunk-logo", self.css)
        self.assertIn('class="brand-partner"', self.html)
        self.assertIn('class="brand-divider" aria-hidden="true"', self.html)
        self.assertNotIn('class="brand-by"', self.html)
        self.assertRegex(
            self.css,
            r"\.brand-partner \{[^}]*align-items: center;",
        )
        self.assertIn(
            ".brand-partner .splunk-logo {\n  height: 20px;\n}",
            self.css,
        )
        self.assertNotIn("splunk&gt;", self.html)
        self.assertNotRegex(
            self.html,
            r'<img[^>]+src="https?://[^\"]*(?:splunk|logo)[^\"]*\.(?:svg|png)',
        )

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
    def test_tab_controller_wires_click_and_keyboard_navigation(self):
        driver = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let focused = null;
const makeTab = (id, selected) => ({
  dataset: { tabTarget: id }, tabIndex: selected ? 0 : -1,
  attrs: { 'aria-selected': String(selected) }, listeners: {},
  setAttribute(name, value) { this.attrs[name] = value; },
  addEventListener(name, listener) { this.listeners[name] = listener; },
  focus() { focused = this; },
  dispatch(name, event = {}) { this.listeners[name](event); },
});
const tabs = ['first', 'second', 'third'].map((id, index) => makeTab(id, index === 0));
const panels = Object.fromEntries(tabs.map((tab, index) =>
  [tab.dataset.tabTarget, { hidden: index !== 0 }]));
const group = { querySelectorAll(selector) {
  assert.equal(selector, '[role="tab"]');
  return tabs;
} };
const documentRoot = {
  querySelectorAll(selector) {
    assert.equal(selector, '[data-tab-group]');
    return [group];
  },
  getElementById(id) { return panels[id]; },
};
const window = {};
vm.runInNewContext(fs.readFileSync('./docs/tab-controller.js', 'utf8'), { window });
window.TokenMeterTabs.setupAll(documentRoot);
const check = selected => {
  tabs.forEach((tab, index) => {
    assert.equal(tab.attrs['aria-selected'], String(index === selected));
    assert.equal(tab.tabIndex, index === selected ? 0 : -1);
    assert.equal(panels[tab.dataset.tabTarget].hidden, index !== selected);
  });
};
tabs[1].dispatch('click');
check(1);
for (const [from, key, expected] of [
  [2, 'ArrowRight', 0], [0, 'ArrowLeft', 2],
  [2, 'Home', 0], [0, 'End', 2],
]) {
  let prevented = false;
  tabs[from].dispatch('keydown', { key, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  check(expected);
  assert.equal(focused, tabs[expected]);
}
let prevented = false;
tabs[2].dispatch('keydown', { key: 'Enter', preventDefault() { prevented = true; } });
assert.equal(prevented, false);
check(2);
"""
        subprocess.run(
            ["node", "-e", driver], cwd=ROOT, check=True,
            capture_output=True, text=True,
        )


ARTICLE_URL = "https://splunk.github.io/token-meter/token-optimization.html"
ATTRIBUTION = "one engineer's laptop, estimated at public list prices"


class FindingsAndArticleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (SITE / "index.html").read_text(encoding="utf-8")
        cls.article = (SITE / "token-optimization.html").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.llms = (SITE / "llms.txt").read_text(encoding="utf-8")
        cls.parser = _SiteParser()
        cls.parser.feed(cls.article)

    def test_readme_teases_findings_after_honest_numbers(self):
        honest = self.readme.index("**Honest numbers.**")
        findings = self.readme.index("## Things your bill won't tell you")
        quick_start = self.readme.index("## Quick Start")
        self.assertLess(honest, findings)
        self.assertLess(findings, quick_start)
        section = self.readme[findings:quick_start]
        self.assertEqual(len(re.findall(r"^- ", section, re.M)), 4)
        self.assertIn(ATTRIBUTION, section.lower())
        self.assertIn(
            f"[Read: Five levers that move your coding-agent bill]({ARTICLE_URL})",
            section,
        )

    def test_index_findings_strip_links_to_the_article(self):
        hero = self.index.index('<section class="hero"')
        strip = self.index.index('<section class="findings-section" id="findings"')
        product = self.index.index('<section class="proof-section"')
        self.assertLess(hero, strip)
        self.assertLess(strip, product)
        section = self.index[strip:product]
        self.assertEqual(section.count('class="finding"'), 3)
        self.assertIn('href="token-optimization.html"', section)
        self.assertIn(ATTRIBUTION, section.lower())
        nav = self.index.split('class="site-nav"', 1)[1].split("</nav>", 1)[0]
        footer = self.index.split('class="footer-links"', 1)[1].split("</div>", 1)[0]
        self.assertIn('href="token-optimization.html"', nav)
        self.assertIn('href="token-optimization.html"', footer)
        self.assertIn(f"]({ARTICLE_URL})", self.llms)

    def test_article_has_metadata_landmarks_and_one_heading(self):
        for metadata in (
            "<title>Five levers that move your coding-agent bill — Token Meter</title>",
            f'rel="canonical" href="{ARTICLE_URL}"',
            f'property="og:url" content="{ARTICLE_URL}"',
            'property="og:type" content="article"',
            'property="og:image" content="https://splunk.github.io/token-meter/images/dashboard.png"',
            'name="twitter:card" content="summary_large_image"',
            'rel="icon" type="image/svg+xml" href="favicon.svg"',
            'rel="describedby" href="llms.txt"',
            'name="description" content="',
        ):
            self.assertIn(metadata, self.article)
        self.assertEqual(len(re.findall(r"<h1\b", self.article)), 1)
        for landmark in ("<header", "<nav", "<main", "<footer", "<article"):
            self.assertIn(landmark, self.article)
        self.assertIn('href="#main-content"', self.article)
        for href in self.parser.links:
            if href.startswith("#"):
                self.assertIn(href[1:], self.parser.ids, href)

    def test_article_covers_the_five_levers_in_order_with_caveats(self):
        body = self.article.split('class="article-body"', 1)[1]
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        positions = [
            text.index(lever) for lever in (
                "Why small token shares cost the most",
                "Model and reasoning", "Task context", "Prompt caching",
                "Outcome-focused prompts", "Avoid rework",
                "Change one thing, then measure", "Can I trust these numbers",
            )
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(ATTRIBUTION, text.lower())
        self.assertIn("git clone https://github.com/splunk/token-meter.git", text)
        words = len(re.sub(r"<script.*?</script>|<head>.*?</head>", "",
                           self.article, flags=re.S).split())
        self.assertGreater(words, 1000)

    def test_article_uses_only_local_assets(self):
        references = set(self.parser.stylesheets + self.parser.scripts)
        references.update(image["src"] for image in self.parser.images)
        self.assertEqual(
            sorted(references),
            sorted({"styles.css", "tab-controller.js", "script.js",
                    "images/logo-splunk-acc-rgb-w.png"}),
        )
        for reference in references:
            self.assertTrue((SITE / reference).is_file(), reference)
        for href in self.parser.links:
            if href.startswith(("#", "https://", "http://127.0.0.1")):
                continue
            target = href.split("#", 1)[0]
            if target in ("", "./"):
                target = "index.html"
            elif target.endswith("/"):
                target += "index.html"
            self.assertTrue((SITE / target).is_file(), href)
        self.assertNotRegex(self.article, r"\son[a-z]+=\"")
        self.assertNotIn("docs/talk", self.article)


TALK_FILES = (
    "talk/index.html", "talk/fonts/OFL-Tektur.txt", "talk/fonts/Tektur-Variable.ttf",
    "talk/img/splunk-logo.png",
) + tuple(
    f"talk/img/live-{name}.jpg" for name in (
        "compare", "efficiency", "git", "models", "sessions", "spend",
        "subagents", "tools", "work",
    )
) + tuple(
    f"talk/img/theme-{name}.jpg" for name in (
        "afterglow", "classic", "ferrous", "monolith", "nebula", "omarchy",
        "phosphor", "voltage",
    )
)


class TalkDeckContractTests(unittest.TestCase):
    def test_deck_uses_only_local_assets_and_links_back_to_site(self):
        deck = (SITE / "talk/index.html").read_text(encoding="utf-8")
        references = set(re.findall(r'src="([^"]+)"', deck))
        references.update(re.findall(r'url\("?([^")]+)"?\)', deck))
        self.assertEqual(
            sorted(references),
            sorted(path.removeprefix("talk/") for path in TALK_FILES
                   if path.endswith((".jpg", ".png", ".ttf"))),
        )
        for reference in references:
            self.assertTrue((SITE / "talk" / reference).is_file(), reference)
        self.assertNotRegex(deck, r'(?:src|href)="//|url\(\s*[\'"]?(?:https?:)?//')
        self.assertIn(
            '<a class="knowmore" href="https://splunk.github.io/token-meter/">', deck
        )


class GitHubPagesBranchSourceContractTests(unittest.TestCase):
    def test_exact_branch_source_contains_every_local_reference(self):
        approved_images = (
            "dashboard.png", "logo-splunk-acc-rgb-w.png", "spend.png",
            "tool-analytics.png",
        )
        deployed = _SiteParser()
        deployed.feed((SITE / "index.html").read_text(encoding="utf-8"))
        local_references = set(deployed.stylesheets + deployed.scripts)
        local_references.update(image["src"] for image in deployed.images)
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
        css = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", css)
        self.assertNotRegex(css, r"url\(\s*['\"]?(?:https?:)?//")

        tracked_site_files = subprocess.check_output(
            ["git", "ls-files", "--", "docs"], cwd=ROOT, text=True,
        ).splitlines()
        self.assertEqual(
            sorted(str(Path(path).relative_to("docs")) for path in tracked_site_files),
            sorted(
                [".nojekyll", "index.html", "token-optimization.html",
                 "styles.css", "tab-controller.js",
                 "script.js", "favicon.svg", "robots.txt", "sitemap.xml",
                 "llms.txt"]
                + ["images/" + image for image in approved_images]
                + list(TALK_FILES)
            ),
        )
        for path in tracked_site_files:
            self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
