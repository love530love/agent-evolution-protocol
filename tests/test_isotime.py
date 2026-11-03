"""One parser for ISO-8601, and a ratchet so it stays the only one.

CI has been red since 2026-11-02 because of a single line in a test module:

    NOW = datetime.fromisoformat("2026-11-02T10:00:05Z")

Python 3.11 returns an aware datetime for that.  Python 3.10 raises
``ValueError``, and because the line ran at import time the whole module failed
to import -- ``ImportError: Failed to import test module`` with the real reason
buried one traceback deeper.  The library had already learned this and wrote
``.replace("Z", "+00:00")`` by hand in seven places.  Seven copies of a
convention is a convention that gets copied an eighth time without the replace.

So: ``isotime.parse_iso`` is the one parser, and ``NoZLiteralTests`` refuses a
bare ``fromisoformat`` on any literal that ends in ``Z``, anywhere in the tree.
The ratchet is on the *literal* rather than the call because a call on a
variable cannot be judged statically, and a rule that cannot be checked is a
rule nobody follows.
"""

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_evolution_protocol.isotime import parse_iso, parse_iso_or_none, to_utc

REPO = Path(__file__).resolve().parents[1]
EXPECTED = datetime(2026, 11, 2, 10, 0, 5, tzinfo=timezone.utc)


class ParseIsoTests(unittest.TestCase):
    def test_accepts_the_zulu_suffix(self):
        # The one that broke 3.10.
        self.assertEqual(parse_iso("2026-11-02T10:00:05Z"), EXPECTED)

    def test_accepts_lowercase_zulu(self):
        self.assertEqual(parse_iso("2026-11-02T10:00:05z"), EXPECTED)

    def test_accepts_an_offset_without_a_colon(self):
        # Also rejected by 3.10, accepted by 3.11.  Same class of bug.
        self.assertEqual(parse_iso("2026-11-02T10:00:05+0000"), EXPECTED)

    def test_accepts_an_offset_with_a_colon(self):
        self.assertEqual(parse_iso("2026-11-02T10:00:05+00:00"), EXPECTED)

    def test_fractional_seconds_survive(self):
        parsed = parse_iso("2026-11-02T10:00:05.500000Z")
        self.assertEqual(parsed.microsecond, 500000)

    def test_a_naive_timestamp_stays_naive_by_default(self):
        # Inventing a timezone is how two processes end up eight hours apart
        # while both believe they agree.
        self.assertIsNone(parse_iso("2026-11-02T10:00:05").tzinfo)

    def test_a_naive_timestamp_can_be_declared_utc(self):
        self.assertEqual(parse_iso("2026-11-02T10:00:05", assume_utc=True), EXPECTED)

    def test_garbage_raises_value_error(self):
        for bad in ("not a time", "", "   ", "2026-13-45T99:99:99Z"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    parse_iso(bad)

    def test_a_non_string_raises_value_error_rather_than_attribute_error(self):
        # Callers catch ValueError.  An AttributeError from ``None.strip()``
        # would escape every one of them.
        for bad in (None, 17, [], b"2026-11-02T10:00:05Z"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    parse_iso(bad)


class ParseIsoOrNoneTests(unittest.TestCase):
    def test_bad_input_becomes_none(self):
        for bad in (None, "", "   ", "not a time", 17):
            with self.subTest(value=bad):
                self.assertIsNone(parse_iso_or_none(bad))

    def test_good_input_is_parsed(self):
        self.assertEqual(parse_iso_or_none("2026-11-02T10:00:05Z"), EXPECTED)


class ToUtcTests(unittest.TestCase):
    def test_naive_gains_utc(self):
        self.assertEqual(to_utc(datetime(2026, 11, 2, 10, 0, 5)), EXPECTED)

    def test_aware_is_left_alone(self):
        original = datetime(2026, 11, 2, 10, 0, 5, tzinfo=timezone.utc)
        self.assertEqual(to_utc(original), original)


class NoZLiteralTests(unittest.TestCase):
    """The ratchet: no bare ``fromisoformat`` on a literal ending in ``Z``.

    Judged on the syntax tree, not on text.  A regex also fires on the
    docstring that explains the rule and on the sample string this test feeds
    its own scanner -- a guard that cannot tell code from prose ends up either
    disabled or ignored.  ``ast`` sees a ``Call`` whose first argument is a
    string constant; a docstring is not a call.
    """

    SKIP_DIRS = {".git", ".tools", "__pycache__", "node_modules", ".workbuddy-ai",
                 "coordination", "build", "dist"}

    @staticmethod
    def offences(source: str):
        tree = ast.parse(source)
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "fromisoformat":
                continue
            first = node.args[0]
            if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and first.value.strip().lower().endswith("z")):
                found.append((node.lineno, first.value))
        return found

    def _sources(self):
        for path in REPO.rglob("*.py"):
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            yield path

    def test_no_module_parses_a_zulu_literal_directly(self):
        offenders = []
        scanned = 0
        for path in self._sources():
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                found = self.offences(text)
            except SyntaxError:
                continue
            for line, value in found:
                offenders.append(f"{path.relative_to(REPO)}:{line}: {value!r}")
        # Report the scan size as well as the verdict: a broken scanner and a
        # clean tree must not produce the same output.
        self.assertGreater(scanned, 100, "the scanner found almost no files; it is broken")
        self.assertEqual(offenders, [],
                         "use isotime.parse_iso instead of fromisoformat on a literal "
                         "ending in Z (rejected by Python 3.10):\n"
                         + "\n".join(offenders))

    def test_the_scanner_would_catch_a_real_offence(self):
        # A guard that has never been seen to fire is not a guard.
        self.assertEqual(
            self.offences('NOW = datetime.fromisoformat("2026-11-02T10:00:05Z")'),
            [(1, "2026-11-02T10:00:05Z")])
        self.assertEqual(
            self.offences('NOW = datetime.fromisoformat("2026-11-02T10:00:05")'), [])

    def test_the_scanner_ignores_prose(self):
        # The docstring that documents the rule must not trip the rule.
        self.assertEqual(self.offences(
            '"""Never write datetime.fromisoformat("2026-11-02T10:00:05Z")."""'), [])


if __name__ == "__main__":
    unittest.main()
