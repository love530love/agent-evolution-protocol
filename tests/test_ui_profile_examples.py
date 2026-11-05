"""The distributable UI profile templates must stay distributable.

``coordination/ui_profiles/`` is gitignored because a profile holds two facts
that are not the same kind of fact: what is true about the client, and what is
true about this machine and this person.  The templates in ``examples/`` carry
only the first kind.

Two failure modes are worth a test rather than a convention:

  * a real absolute path, a real conversation title or a probe pid getting
    copied into a template.  That is a disclosure in a public repository, and
    it is exactly the kind of detail that survives a careful manual scrub.
  * a template drifting away from the profile it claims to describe, so a
    person instantiating it gets last month's calibration with this month's
    confidence.

The exporter is the single source for the templates, so the second failure is
caught by regenerating and comparing.  The first is caught structurally.
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "ui_profiles"
LOCAL = ROOT / "coordination" / "ui_profiles"

# A drive letter or a POSIX home directory: neither belongs in a template.
ABSOLUTE_PATH = re.compile(r"(?:^|[\\s\"'])(?:[A-Za-z]:[\\/]|/Users/|/home/)")
PID = re.compile(r"\bpid \d+")
PLACEHOLDER = re.compile(r"^<REPLACE:")


def _templates() -> dict[str, dict]:
    if not EXAMPLES.is_dir():
        return {}
    out = {}
    for path in sorted(EXAMPLES.glob("*.json")):
        out[path.name] = json.loads(path.read_text(encoding="utf-8"))
    return out


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _load_exporter():
    path = ROOT / "scripts" / "export_ui_profile_examples.py"
    spec = importlib.util.spec_from_file_location("aep_export_ui_profiles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TemplateShapeTests(unittest.TestCase):
    def setUp(self):
        self.templates = _templates()
        if not self.templates:
            self.skipTest("no templates committed yet")

    def test_every_template_names_its_client_and_transport(self):
        for name, profile in self.templates.items():
            with self.subTest(template=name):
                for key in ("agent", "process", "title", "model_marker"):
                    self.assertTrue(str(profile.get(key, "")).strip(),
                                    f"{name}: {key} must be set")
                self.assertIn(profile.get("transport"), {"uia", "ocr"}, name)
                self.assertIn(profile.get("send_mode", "enter"),
                              {"enter", "button", "auto"}, name)

    def test_an_ocr_template_names_an_exact_composer_hint(self):
        # load_profile refuses this combination at runtime; failing here names
        # the template instead of the machine that tried to use it.
        for name, profile in self.templates.items():
            if profile.get("transport") != "ocr":
                continue
            with self.subTest(template=name):
                self.assertTrue(str(profile.get("composer_hint", "")).strip(), name)

    def test_a_late_interject_control_also_declares_that_it_is_allowed(self):
        # The two fields are read together: interject_after_send says the
        # control appears late, allow_busy_interject says this profile wants the
        # urgent path at all.  One without the other is a half-made claim.
        for name, profile in self.templates.items():
            if not profile.get("interject_after_send"):
                continue
            with self.subTest(template=name):
                self.assertTrue(profile.get("allow_busy_interject"), name)
                self.assertTrue(profile.get("interject_hints"), name)


class TemplatePrivacyTests(unittest.TestCase):
    """A template that carries local facts is a disclosure, not a template."""

    def setUp(self):
        self.templates = _templates()
        if not self.templates:
            self.skipTest("no templates committed yet")

    def test_the_two_local_fields_are_placeholders(self):
        for name, profile in self.templates.items():
            for key in ("exe", "session_marker"):
                with self.subTest(template=name, field=key):
                    value = str(profile.get(key, ""))
                    self.assertRegex(value, PLACEHOLDER,
                                     f"{name}: {key} must still be a placeholder")
            with self.subTest(template=name):
                self.assertEqual(sorted(profile.get("_fill_in_locally", [])),
                                 ["exe", "session_marker"])

    def test_no_template_carries_an_absolute_path(self):
        for name, profile in self.templates.items():
            for value in _strings(profile):
                with self.subTest(template=name):
                    self.assertIsNone(
                        ABSOLUTE_PATH.search(value),
                        f"{name}: a local path survived the export: {value[:80]}")

    def test_no_template_carries_a_probe_pid(self):
        # EXECUTION_PLANE_UPGRADE_PLAN asks for templates with no PID, and a pid
        # inside a prose note is the one that gets past a manual scrub.
        for name, profile in self.templates.items():
            for value in _strings(profile):
                with self.subTest(template=name):
                    self.assertIsNone(PID.search(value),
                                      f"{name}: a pid survived the export: {value[:80]}")


class TemplateFreshnessTests(unittest.TestCase):
    """A template that drifts from its profile teaches the wrong calibration."""

    def setUp(self):
        self.templates = _templates()
        if not self.templates:
            self.skipTest("no templates committed yet")
        if not LOCAL.is_dir() or not any(LOCAL.glob("*.json")):
            # A machine without local profiles cannot answer this, and CI is one
            # of those machines.  Say so rather than passing silently as if the
            # templates had been checked.
            self.skipTest("no local profiles on this machine to compare against")

    def test_templates_match_what_the_exporter_would_write(self):
        exporter = _load_exporter()
        for path in sorted(LOCAL.glob("*.json")):
            profile = json.loads(path.read_text(encoding="utf-8"))
            agent = str(profile.get("agent") or path.stem)
            target = EXAMPLES / f"{agent}.json"
            with self.subTest(agent=agent):
                self.assertTrue(target.exists(), f"{agent}: no committed template")
                self.assertEqual(
                    target.read_text(encoding="utf-8"), exporter.render(profile),
                    f"{agent}: template is stale; run "
                    f"scripts/export_ui_profile_examples.py")


if __name__ == "__main__":
    unittest.main()
