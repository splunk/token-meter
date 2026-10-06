"""Design-system contract for the dashboard stylesheet in page.html."""

import re
import unittest
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "page.html").read_text()
CSS = PAGE[PAGE.index("<style>") + len("<style>"):PAGE.index("</style>")]
JS = PAGE[PAGE.index("<script>") + len("<script>"):PAGE.rindex("</script>")]


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


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


if __name__ == "__main__":
    unittest.main()
