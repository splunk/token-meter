"""Design-system contract for the dashboard stylesheet in page.html."""

import re
import unittest
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "page.html").read_text()
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
RGBA_LITERAL_BASELINE = 98
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


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def scale_px(prefix):
    return {name: float(value.strip()[:-2]) for name, value in TOKENS.items()
            if name.startswith(prefix) and value.strip().endswith("px")}


class ScaleTokenTest(unittest.TestCase):
    SIZE_OK = re.compile(r"^var\(--fs-[\w-]+\)(!important)?$|^inherit$|^0$|^[\d.]+(em|%)$")

    def test_font_sizes_use_scale_tokens(self):
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop == "font-size" and not self.SIZE_OK.match(value.strip())})
        self.assertEqual(offenders, [])

    def test_font_shorthands_use_scale_tokens(self):
        offenders = sorted({value.strip() for prop, value in DECLARATIONS
                            if prop == "font" and value.strip() != "inherit"
                            and re.search(r"\d+(\.\d+)?px|\b[1-9]00\b|\b\d{3}\b|Menlo|apple-system", value)})
        self.assertEqual(offenders, [])

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
