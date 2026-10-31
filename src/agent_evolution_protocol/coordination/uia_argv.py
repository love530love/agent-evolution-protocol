"""Carry non-ASCII UI parameters past the console code page.

The failure this module fixes is not subtle and it is not rare: this host runs
PowerShell with ``[Console]::InputEncoding = ibm437``, so any Chinese text in an
argument arrives inside the script already reduced to ``?``.  A composer hint of
``长按鼠标语音输入文字`` becomes ``???????`` and can never match OCR output, and a
message body becomes a line of question marks typed at the Agent.

No amount of quoting fixes this -- the loss happens when the command line is
decoded, before the script runs.  The only reliable channel is a file the child
reads itself, so every non-ASCII value is moved into a UTF-8 JSON document and
passed as ``-ParamFile``.  ASCII-only calls keep the plain argv path, so this
changes nothing for the common case.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

#: Parameters the UI script declares as ``[string[]]``.  They are stored as
#: JSON arrays so PowerShell binds them without a comma-splitting guess.
LIST_FLAGS = frozenset({"InterjectHints", "ComposerHints"})
PARAM_FILE_FLAG = "-ParamFile"


def non_ascii(value) -> bool:
    return any(ord(char) > 127 for char in str(value))


def split_args(argv):
    """Split ``argv`` into an ASCII-safe list plus UTF-8 overrides.

    A flag is only moved when it has a value: ``-OcrVerify`` and friends stay
    where they are.
    """
    kept, overrides = [], {}
    index = 0
    while index < len(argv):
        token = str(argv[index])
        if token.startswith("-") and index + 1 < len(argv):
            value = str(argv[index + 1])
            if not value.startswith("-") and non_ascii(value):
                name = token.lstrip("-")
                if name in LIST_FLAGS:
                    overrides[name] = [part for part in value.split(",") if part]
                else:
                    overrides[name] = value
                index += 2
                continue
        kept.append(token)
        index += 1
    return kept, overrides


def run(argv, *, timeout, env=None, encoding="utf-8"):
    """Run the UI script, routing non-ASCII parameters through a UTF-8 file."""
    kept, overrides = split_args(argv)
    if not overrides:
        return subprocess.run(kept, text=True, encoding=encoding, errors="replace",
                              capture_output=True, timeout=timeout, check=False, env=env)
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
    try:
        with handle:
            json.dump(overrides, handle, ensure_ascii=False)
        return subprocess.run([*kept, PARAM_FILE_FLAG, handle.name], text=True,
                              encoding=encoding, errors="replace", capture_output=True,
                              timeout=timeout, check=False, env=env)
    finally:
        Path(handle.name).unlink(missing_ok=True)
