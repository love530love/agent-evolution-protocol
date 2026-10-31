"""Non-ASCII UI parameters must survive the child console code page.

On this host PowerShell reports ``[Console]::InputEncoding = ibm437``, so any
Chinese value in argv arrives inside the script as ``?``.  These tests pin the
two halves of the workaround: the split that moves values into a UTF-8 file, and
the fact that ASCII-only calls are left completely alone.
"""
import json
import unittest
from pathlib import Path

from agent_evolution_protocol.coordination import uia_argv


class SplitArgsTests(unittest.TestCase):
    def test_chinese_value_moves_to_the_override_file(self):
        kept, overrides = uia_argv.split_args(
            ["-File", "ui.ps1", "-Text", "请复核这条结论", "-Mode", "deliver"])
        self.assertNotIn("请复核这条结论", kept)
        self.assertEqual(overrides, {"Text": "请复核这条结论"})
        # Switches and ASCII pairs are untouched.
        self.assertIn("-Mode", kept)
        self.assertIn("deliver", kept)

    def test_ascii_only_calls_keep_the_plain_argv_path(self):
        argv = ["-File", "ui.ps1", "-Text", "hello", "-Mode", "observe"]
        kept, overrides = uia_argv.split_args(argv)
        self.assertEqual(kept, argv)
        self.assertEqual(overrides, {})

    def test_list_parameters_become_json_arrays(self):
        _, overrides = uia_argv.split_args(
            ["-InterjectHints", "插话,继续执行并发送,Send while running"])
        self.assertEqual(overrides["InterjectHints"],
                         ["插话", "继续执行并发送", "Send while running"])

    def test_switch_is_never_mistaken_for_a_value(self):
        kept, overrides = uia_argv.split_args(["-OcrVerify", "-Mode", "observe"])
        self.assertEqual(overrides, {})
        self.assertEqual(kept, ["-OcrVerify", "-Mode", "observe"])

    def test_non_ascii_detection(self):
        self.assertTrue(uia_argv.non_ascii("插话"))
        self.assertTrue(uia_argv.non_ascii("café"))
        self.assertFalse(uia_argv.non_ascii("AEP-ACK:EVT-1"))
        self.assertFalse(uia_argv.non_ascii(""))


class RunTests(unittest.TestCase):
    """Exercise ``run`` against a throwaway interpreter, not a real client."""

    def test_override_file_is_written_utf8_and_removed(self):
        import sys
        # The child prints ASCII-escaped JSON on purpose: its stdout encoding is
        # the platform locale (cp1252 on a CI Windows runner), so printing raw
        # Chinese would fail for a reason that has nothing to do with this
        # module.  The file is still read as UTF-8, so a wrong encoding on
        # write would break the decode and change the decoded value.
        script = (
            "import json,sys,io\n"
            "argv=sys.argv[1:]\n"
            "path=argv[argv.index('-ParamFile')+1]\n"
            "data=json.load(io.open(path,encoding='utf-8'))\n"
            "print(json.dumps(data,ensure_ascii=True))\n"
        )
        result = uia_argv.run([sys.executable, "-c", script, "-Text", "中文正文"],
                              timeout=60)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout.strip()), {"Text": "中文正文"})

    def test_ascii_call_passes_no_param_file(self):
        import sys
        script = "import sys;print('-ParamFile' in sys.argv)"
        result = uia_argv.run([sys.executable, "-c", script, "-Text", "plain"],
                              timeout=60)
        self.assertEqual(result.stdout.strip(), "False")

    def test_temp_file_does_not_survive_the_call(self):
        import sys
        script = (
            "import json,sys\n"
            "argv=sys.argv[1:]\n"
            "print(argv[argv.index('-ParamFile')+1])\n"
        )
        result = uia_argv.run([sys.executable, "-c", script, "-Text", "中文"], timeout=60)
        written = result.stdout.strip()
        self.assertTrue(written)
        self.assertFalse(Path(written).exists())


if __name__ == "__main__":
    unittest.main()
