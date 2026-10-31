"""Pin the OCR wiring that was silently broken for months.

Two defects are guarded here, and neither is reachable by a normal unit test:
the OCR itself is a WinRT island that only Windows PowerShell 5.1 can call, and
the values that reach it are assembled in Python.  So these tests check the two
things that actually went wrong:

1. the requested language reached only one of ten call sites, so nine of them
   quietly used the user-profile recognizer;
2. Windows OCR emits a space between adjacent CJK glyphs, so a marker written
   "插话" never matched the text "插 话".

The first is checked by reading the script's own default; the second by running
the script's own pattern over a synthetic string, so the test follows the code
instead of duplicating it.
"""
from __future__ import annotations

import re
from pathlib import Path
import unittest

from agent_evolution_protocol.coordination import ui_fallback

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "src/agent_evolution_protocol/coordination/wake_adapters"
COMPAT = (ADAPTERS / "windows_ocr_compat.ps1").read_text(encoding="utf-8")
SESSION = (ADAPTERS / "uia_session.ps1").read_text(encoding="utf-8")


class CjkNormalizationTests(unittest.TestCase):
    def setUp(self):
        match = re.search(r"\$cjk = '([^']+)'", COMPAT)
        self.assertIsNotNone(match, "the CJK character class disappeared")
        self.cjk = match.group(1)
        self.pattern = f"(?<={self.cjk})\\s+(?={self.cjk})"

    def test_spaces_between_chinese_characters_are_collapsed(self):
        self.assertEqual(re.sub(self.pattern, "", "插 话 发 送"), "插话发送")

    def test_latin_word_spacing_is_preserved(self):
        text = "Send while running"
        self.assertEqual(re.sub(self.pattern, "", text), text)

    def test_mixed_text_keeps_english_words_intact(self):
        # Only whitespace *between two CJK characters* is dropped.  Gluing a
        # Chinese character to an English word would corrupt both, so the
        # boundary space survives on purpose.
        self.assertEqual(re.sub(self.pattern, "", "点 击 Send while running 发 送"),
                         "点击 Send while running 发送")

    def test_the_compat_island_actually_applies_it_to_both_outputs(self):
        # Per-line text feeds marker matching; the joined text feeds the
        # transcript.  Normalising only one of them fixes half the symptom.
        self.assertIn("Normalize-OcrText ([string]$_.Text)", COMPAT)
        self.assertIn("Normalize-OcrText ([string]$result.Text)", COMPAT)


class LanguageWiringTests(unittest.TestCase):
    def test_every_ocr_call_inherits_the_requested_language(self):
        # The original bug: only one call site passed -Language, so the other
        # nine used en-US and read Chinese as Latin look-alikes.
        self.assertIn("if (-not $Language) { $Language = $script:OcrLanguage }", SESSION)

    def test_the_driver_forwards_the_profile_language(self):
        # Built inline on purpose: coordination/ui_profiles/*.json is a live
        # runtime file and is not committed, so reading it made this test pass
        # on one machine and fail with FileNotFoundError everywhere else.
        profile = {"agent": "qoder", "process": "Qoder", "title": "Qoder",
                   "exe": "Qoder.exe", "ocr_language": "zh-Hans-CN"}
        args = ui_fallback.PowerShellUiaDriver._target(profile)
        self.assertIn("-OcrLanguage", args)
        self.assertEqual(args[args.index("-OcrLanguage") + 1], "zh-Hans-CN")

    def test_a_profile_without_a_language_does_not_invent_one(self):
        profile = {"agent": "x", "process": "x", "title": "x", "exe": "x"}
        args = ui_fallback.PowerShellUiaDriver._target(profile)
        self.assertNotIn("-OcrLanguage", args)

    def test_observe_reports_where_the_transcript_came_from(self):
        # An Electron window has no UIA text, so "0 characters" used to mean
        # "nothing to read" when it actually meant "I could not see".
        self.assertIn("transcript_source", SESSION)
        self.assertIn("$transcriptSource = 'ocr'", SESSION)


class OutputEncodingTests(unittest.TestCase):
    """The OCR result has to survive leaving its own process.

    Measured on this host: a piped call turned every Chinese character into
    "?" and turned two unrecognised glyphs into raw 0x07 bytes.  A control byte
    inside a JSON string makes the object invalid, so any consumer parsing it
    across a process boundary failed outright -- and the "?" version failed
    silently, because a marker written in Chinese simply never matched.
    """

    def test_the_island_emits_utf8_instead_of_trusting_the_code_page(self):
        self.assertIn("[System.Text.UTF8Encoding]::new($false).GetBytes($json", COMPAT)
        self.assertIn("[Console]::OpenStandardOutput()", COMPAT)

    def test_the_island_can_hand_the_result_over_as_a_file(self):
        # A file removes the console code page from the path entirely, which is
        # what the in-process caller needs: reading UTF-8 bytes back with the
        # parent's code page produced mojibake that still looked like a
        # non-empty transcript.
        self.assertIn("[string]$OutFile = ''", COMPAT)
        self.assertIn("[System.IO.File]::WriteAllText($OutFile, $json", COMPAT)

    def test_the_session_reads_that_file_as_utf8(self):
        self.assertIn("-OutFile $ocrFile", SESSION)
        self.assertIn("[IO.File]::ReadAllText($ocrFile, [System.Text.UTF8Encoding]::new($false))",
                      SESSION)

    def test_the_failure_path_uses_the_same_channel(self):
        # A caller that parses stdout cannot tell "no text" from "the engine
        # died" unless the failure is also a well-formed object.
        self.assertIn("$failure = [pscustomobject]@{ Text = ''; Lines = @()", COMPAT)
        self.assertIn("[System.Text.UTF8Encoding]::new($false).GetBytes($failure", COMPAT)


if __name__ == "__main__":
    unittest.main()
