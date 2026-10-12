"""Design-system contract for the dashboard stylesheet in page.html."""

import itertools
import math
import re
import unittest
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[2] / "page.html").read_text()
CSS = PAGE[PAGE.index("<style>") + len("<style>"):PAGE.index("</style>")]
JS = PAGE[PAGE.index("<script>") + len("<script>"):PAGE.rindex("</script>")]


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


RULES = strip_comments(CSS)
ROOT_MATCH = re.match(r"\s*:root\{(.*?)\n\}", RULES, re.S)
ROOT = ROOT_MATCH.group(1) if ROOT_MATCH else ""
OUTSIDE_ROOT = RULES[ROOT_MATCH.end():] if ROOT_MATCH else RULES
TOKENS = dict(re.findall(r"--([\w-]+):([^;]+);", ROOT))
RULE_BODY = re.sub(r"@font-face\{[^}]*\}", "", OUTSIDE_ROOT)
DECLARATIONS = re.findall(r"([\w-]+)\s*:\s*([^;{}]+)", re.sub(r"[^{}]*\{", "{", RULE_BODY))
# Raw rgb()/rgba() literals left outside the token root. Lower this as
# surfaces move onto tokens; never raise it.
RGBA_LITERAL_BASELINE = 86
# Raw rgb()/rgba()/hsl() literals in the dashboard script. Same rule: only lower it.
JS_COLOR_LITERAL_BASELINE = 10
NAMED_COLOR = re.compile(r"(?<![\w-])(red|blue|green|white|black|gr[ae]y|orange|purple|yellow|pink|cyan|magenta|navy|teal|silver|gold)(?![\w-])")
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


class StylesheetStructureTest(unittest.TestCase):
    def test_stylesheet_has_one_top_level_rule_per_line(self):
        offenders = []
        for number, line in enumerate(strip_comments(CSS).splitlines(), 1):
            depth = closes = 0
            for char in line:
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        closes += 1
            if closes > 1:
                offenders.append(number)
        self.assertEqual(offenders, [], "lines holding several top-level rules")

    def test_one_token_root_opens_the_stylesheet(self):
        self.assertIsNotNone(ROOT_MATCH, "stylesheet must open with :root{")
        self.assertNotIn(":root{", OUTSIDE_ROOT)


class ColorTokenTest(unittest.TestCase):
    def test_hex_colors_only_in_token_root(self):
        offenders = sorted({f"{prop}:{value.strip()[:60]}" for prop, value in DECLARATIONS
                            if HEX.search(value)})
        self.assertEqual(offenders, [])

    def test_rgb_channel_tokens_match_their_hex_token(self):
        for name, channels in TOKENS.items():
            if not name.endswith("-rgb") or name[:-4] not in TOKENS:
                continue
            value = TOKENS[name[:-4]].strip()
            if not value.startswith("#"):
                continue
            hexes = value[1:]
            if len(hexes) == 3:
                hexes = "".join(c * 2 for c in hexes)
            expected = " ".join(str(int(hexes[i:i + 2], 16)) for i in (0, 2, 4))
            with self.subTest(token=name):
                self.assertEqual(channels.strip(), expected)

    def test_no_named_or_hsl_colors(self):
        offenders = sorted({f"{prop}:{value.strip()[:60]}" for prop, value in DECLARATIONS
                            if not prop.startswith("--") and (re.search(r"\bhsla?\(", value) or NAMED_COLOR.search(value))
                            and not prop.startswith(("mask", "-webkit-mask"))})
        self.assertEqual(offenders, [])

    def test_script_color_literal_ratchet(self):
        count = len(re.findall(r"\b(?:rgba?|hsla?)\(\s*\d", JS))
        self.assertLessEqual(count, JS_COLOR_LITERAL_BASELINE)

    def test_theme_compare_colors_match_css_tokens(self):
        compare = re.search(r"compare:\[([^\]]*)\]", JS).group(1)
        values = [v.strip("'\" ") for v in compare.split(",")]
        expected = [TOKENS[name].strip() for name in ("cyan", "violet", "orange", "green")]
        self.assertEqual(values, expected)

    def test_provider_identity_has_one_color_per_runtime(self):
        self.assertEqual([name for name in TOKENS if name.startswith(("harness-", "spend-"))], [])
        families = dict(re.findall(r"^ '?([\w-]+)'?:\['(#[0-9A-Fa-f]{6})'", JS[JS.index("const MODEL_COLORS={"):], re.M))
        for runtime in ("claude", "codex", "cursor", "kiro", "opencode"):
            with self.subTest(runtime=runtime):
                self.assertEqual(families[runtime].lower(), TOKENS[f"provider-{runtime}"].strip().lower())

    def test_model_families_stay_distinct_across_runtimes(self):
        block = JS[JS.index("const MODEL_COLORS={"):JS.index("};", JS.index("const MODEL_COLORS={"))]
        families = {name: re.findall(r"#[0-9A-Fa-f]{6}", shades)
                    for name, shades in re.findall(r"^ '?([\w-]+)'?:\[([^\]]*)\]", block, re.M) if name != "fallback"}
        closest = min((delta_e(a, b), x, a, y, b) for x, y in itertools.combinations(families, 2)
                      for a in families[x] for b in families[y])
        self.assertGreaterEqual(closest[0], 20, f"{closest[1]} {closest[2]} is too close to {closest[3]} {closest[4]}")

    def test_provider_colors_are_readable(self):
        providers = [name for name in TOKENS if name.startswith("provider-") and not name.endswith("-rgb")]
        self.assertTrue(providers)
        for name in providers:
            with self.subTest(token=name):
                self.assertGreaterEqual(contrast(TOKENS[name].strip(), TOKENS["panel3"].strip()), 4.5)
        surface = TOKENS["panel3"].strip()
        for runtime, alpha in re.findall(r"\.badge\.([a-z]+)\{[^}]*background:rgb\(var\(--provider-[a-z]+-rgb\)/([\d.]+)\)", RULES):
            text = TOKENS[f"provider-{runtime}"].strip()
            mixed = "#" + "".join(
                f"{round(int(surface[i:i + 2], 16) * (1 - float(alpha)) + int(text[i:i + 2], 16) * float(alpha)):02x}"
                for i in (1, 3, 5))
            with self.subTest(badge=runtime):
                self.assertGreaterEqual(contrast(text, mixed), 4.5)

    def test_runtime_surfaces_resolve_provider_colors_by_runtime_id(self):
        ids = set(re.search(r"PROVIDER_COLOR_IDS=new Set\(\[([^\]]*)\]\)", JS).group(1).replace("'", "").split(","))
        self.assertEqual(ids, {name[len("provider-"):] for name in TOKENS
                               if name.startswith("provider-") and not name.endswith("-rgb") and name != "provider-other"})
        self.assertIn("const runtimeHarness=session=>providerColor(runtimeId(session));", JS)
        for runtime in ids:
            with self.subTest(runtime=runtime):
                self.assertRegex(RULES, r"\.badge\." + runtime + r"\{")

    def test_rgba_literal_ratchet(self):
        count = len(re.findall(r"\brgba?\(\s*\d", OUTSIDE_ROOT))
        self.assertLessEqual(count, RGBA_LITERAL_BASELINE)


def luminance(hex_value):
    value = hex_value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def lab(hex_value):
    value = hex_value.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def delta_e(a, b):
    return math.dist(lab(a), lab(b))


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def scale_px(prefix):
    return {name: float(value.strip()[:-2]) for name, value in TOKENS.items()
            if name.startswith(prefix) and value.strip().endswith("px")}


class ScaleTokenTest(unittest.TestCase):
    SIZE_OK = re.compile(r"^var\(--fs-[\w-]+\)(!important)?$|^inherit$|^0$")

    def test_font_sizes_use_scale_tokens(self):
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop == "font-size" and not self.SIZE_OK.match(value.strip())})
        self.assertEqual(offenders, [])

    def test_font_shorthands_use_scale_tokens(self):
        shorthand = re.compile(r"^(var\(--fw-\w+\) )?var\(--fs-[\w-]+\)(/[\d.]+)? var\(--font-\w+\)$")
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop == "font" and value.strip() != "inherit"
                            and not shorthand.match(value.strip())})
        self.assertEqual(offenders, [])

    def test_font_families_use_tokens(self):
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop == "font-family" and not re.match(r"^(var\(--font-\w+\)|inherit)$", value.strip())})
        self.assertEqual(offenders, [])

    def test_positive_letter_spacing_is_capped(self):
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop == "letter-spacing" and (
                                (re.fullmatch(r"\.?\d*\.?\d+em", value.strip()) and float(value.strip()[:-2]) > 0.06)
                                or re.fullmatch(r"\.?\d*\.?\d+px", value.strip()))})
        self.assertEqual(offenders, [])

    def test_script_font_shorthands_use_tokens(self):
        offenders = re.findall(r"font:[^;\"`]*", JS)
        offenders = [o for o in offenders
                     if not re.match(r"font:(var\(--fs-[\w-]+\)|\$\{svgTextPx\([^)]*\)\}px) var\(--font-\w+\)$", o)
                     and o.strip() != "font:inherit"]
        self.assertEqual(offenders, [])

    def test_script_inline_font_sizes_meet_floor(self):
        sizes = [float(size) for size in re.findall(r"font-size:\s*([\d.]+)px", JS)]
        self.assertEqual([size for size in sizes if size < 11], [])

    def test_markup_inline_styles_use_tokens(self):
        markup = PAGE[PAGE.index("</style>"):PAGE.index("<script>")]
        styles = re.findall(r'style="([^"]*)"', markup)
        self.assertEqual([style for style in styles if HEX.search(style) or re.search(r"\d+px", style) and "font" in style], [])

    def test_font_weights_use_scale_tokens(self):
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop == "font-weight" and not re.match(r"^(var\(--fw-\w+\)|inherit)(!important)?$", value.strip())})
        self.assertEqual(offenders, [])

    def test_radii_use_scale_tokens(self):
        part = r"(var\(--radius-[\w-]+\)|0|inherit)"
        pattern = re.compile(rf"^{part}( {part}){{0,3}}$")
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop.endswith("radius") and not pattern.match(value.strip())})
        self.assertEqual(offenders, [])

    def test_type_floor_is_eleven_px(self):
        sizes = scale_px("fs-")
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes.values()), 11)

    def test_text_tokens_meet_contrast_on_every_surface(self):
        surfaces = ["bg-deep", "bg", "bg2", "panel", "panel2", "panel3"]
        for text in ["fg", "dim", "faint", "chart-label"]:
            for surface in surfaces:
                with self.subTest(text=text, surface=surface):
                    self.assertGreaterEqual(
                        contrast(TOKENS[text].strip(), TOKENS[surface].strip()), 4.5)


SPACING_SCALE = {0, 1, 2, 3, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 48, 64}
# scroll-padding is checked; scroll-margin is left out because it is an anchor offset for the sticky rail.
SPACING_PROP = re.compile(r"(row-|column-)?gap|(scroll-padding|padding|margin)(-(top|right|bottom|left|inline|block)(-(start|end))?)?")


class SpacingScaleTest(unittest.TestCase):
    def test_spacing_values_on_scale(self):
        offenders = set()
        for prop, value in DECLARATIONS:
            if not SPACING_PROP.fullmatch(prop) or re.search(r"calc|clamp|min\(|max\(", value):
                continue
            for px in re.findall(r"(?<![\w.])-?(\d+(?:\.\d+)?)px", value):
                if float(px) not in SPACING_SCALE:
                    offenders.add(f"{prop}:{value.strip()}")
        self.assertEqual(sorted(offenders), [])


class ScriptPaletteTest(unittest.TestCase):
    def test_js_hex_only_in_theme_and_model_palette(self):
        theme = re.search(r"\nconst THEME=\{.*?\n\};\n", JS, re.S)
        self.assertIsNotNone(theme)
        model = JS[JS.index("// model-color-logic-start"):JS.index("// model-color-logic-end")]
        remainder = JS.replace(theme.group(0), "").replace(model, "")
        self.assertEqual(sorted(set(HEX.findall(remainder))), [])

    def test_fixed_viewbox_charts_scale_their_text(self):
        # Scaled SVG charts size text through svgTextPx so labels never render below the floor.
        for name in ("drawStepsChart", "drawWaitChart", "drawIO", "drawModelTrend", "drawModelMix", "renderModelSpeedChart"):
            body = JS[JS.index(f"function {name}("):]
            body = body[:body.index("\nfunction ", 1)]
            with self.subTest(chart=name):
                self.assertIn("svgTextPx(svg,VW,VH", body)
                self.assertNotRegex(body, r'font-size="\d')

    def test_stretched_svgs_redraw_in_a_pixel_viewbox(self):
        markup = PAGE[PAGE.index("</style>"):PAGE.index("<script>")]
        stretched = set(re.findall(r'<svg[^>]*\bid=([\w-]+)[^>]*preserveAspectRatio="?none', markup))
        stretched |= set(re.findall(r'<svg[^>]*preserveAspectRatio="?none"?[^>]*\bid=([\w-]+)', markup))
        registry = re.search(r"const PIXEL_CHARTS=\{(.*?)\};", JS).group(1)
        registered = set(re.findall(r"'([\w-]+)'", registry))
        self.assertTrue(stretched)
        self.assertEqual(sorted(stretched - registered), [])
        callback = JS[JS.index("const chartResizeObserver=new ResizeObserver("):]
        callback = callback[:callback.index("\n});")]
        groups = re.findall(r"(\w+):\[", registry)
        self.assertEqual([group for group in groups if f"PIXEL_CHARTS.{group}." not in callback], [])

    def test_svg_text_meets_floor(self):
        sizes = [float(size) for size in re.findall(r'font-size="([\d.]+)"', JS)]
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), 11)


# Classes the script assembles at runtime from data (badge kinds, insight action ids). Add one
# here when the backend ships a new id that has its own rule.
RUNTIME_BUILT_CLASSES = {"chatgpt", "fix_or_disable", "narrow_results", "reduce_repeats"}
# Short or :is()-nested legacy names not yet proven dead. This set may only shrink.
LEGACY_UNREFERENCED_CLASSES = {
    "anom", "capReview", "chips", "cols", "costsplit", "hasAttention", "iconBtn", "mini", "reco",
    "separator", "spark",
}
UNREFERENCED_CLASS_ALLOWLIST = RUNTIME_BUILT_CLASSES | LEGACY_UNREFERENCED_CLASSES
ZERO_SPECIFICITY_COMPONENTS = ("emptyState", "metric", "chartTip", "trendDelta", "valueWithDelta")


class ComponentTest(unittest.TestCase):
    def test_components_have_zero_specificity(self):
        for name in ZERO_SPECIFICITY_COMPONENTS:
            selectors = re.findall(r"(?:^|\n)([^{}\n]*\." + name + r"\b[^{}\n]*)\{", RULES)
            component_rules = [sel for sel in selectors if sel.lstrip().startswith(":where(." + name)]
            with self.subTest(component=name):
                self.assertTrue(component_rules, "component rules missing")
                plain = [sel for sel in selectors if re.match(r"\s*\." + name + r"(?![\w-])", sel)]
                self.assertEqual(plain, [], "wrap component selectors in :where() so screens can override them")

    def test_hidden_attribute_still_hides_display_components(self):
        # A component that sets display beats the browser's [hidden] default, so it must restore it.
        for name in ZERO_SPECIFICITY_COMPONENTS:
            sets_display = re.search(r":where\(\." + name + r"\)\{[^}]*display:", RULES)
            uses_hidden = re.search(r"class=\"?[^\">]*(?<![\w-])" + name + r"(?![\w-])[^>]*\bhidden\b", PAGE) or \
                re.search(r"render" + name[0].upper() + name[1:] + r"\(", JS)
            if sets_display and uses_hidden:
                with self.subTest(component=name):
                    self.assertRegex(RULES, r":where\(\." + name + r"\)\[hidden\]\{display:none\}")

    def test_components_are_used_in_markup(self):
        markup_and_script = PAGE[:PAGE.index("<style>")] + PAGE[PAGE.index("</style>"):]
        for name in ZERO_SPECIFICITY_COMPONENTS + ("chip",):
            with self.subTest(component=name):
                self.assertRegex(markup_and_script, r"class=\"?[^\">]*(?<![\w-])" + name + r"(?![\w-])")

    def test_every_stylesheet_class_is_referenced(self):
        classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"url\([^)]*\)|\"[^\"]*\"", "", re.sub(r"\{[^{}]*\}", "{}", RULES))))
        outside = PAGE[:PAGE.index("<style>")] + PAGE[PAGE.index("</style>"):]
        words = set(re.findall(r"[A-Za-z][\w-]*", outside))
        unreferenced = {name for name in classes if name not in words}
        self.assertEqual(sorted(unreferenced - UNREFERENCED_CLASS_ALLOWLIST), [], "dead CSS: remove the rule or use the class")
        self.assertEqual(sorted(UNREFERENCED_CLASS_ALLOWLIST - unreferenced), [], "now referenced or removed: drop it from the allowlist")


class TokenReferenceTest(unittest.TestCase):
    def declared(self):
        names = set(re.findall(r"--([\w-]+)\s*:", RULES))
        names |= set(re.findall(r"setProperty\('--([\w-]+)'", JS))
        names |= set(re.findall(r"--([\w-]+):\$\{", JS))
        names |= set(re.findall(r"['\"`;\s]--([\w-]+):", JS))
        return names

    def test_every_var_reference_resolves(self):
        used = set(re.findall(r"var\(--([\w-]+)(?![\w$-])", RULES + JS))
        used |= set(re.findall(r"cssVar\('--([\w-]+)'\)", JS))
        self.assertEqual(sorted(used - self.declared()), [])

    def test_every_root_token_is_used(self):
        used = set(re.findall(r"var\(--([\w-]+)", RULES + JS))
        used |= set(re.findall(r"cssVar\('--([\w-]+)'\)", JS))
        prefixes = tuple(re.findall(r"(?:cssVar\(`|var\()--([\w-]+-)\$\{", JS))
        unused = [name for name in TOKENS if name not in used and not name.startswith(prefixes or ("\0",))]
        self.assertEqual(sorted(unused), [])


if __name__ == "__main__":
    unittest.main()
