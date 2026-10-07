"""Run the hook's tests on any Python, without pytest.

    pythonX run_hook_tests.py

For a bare interpreter (a verified source build) where installing pytest would
mean fetching new code. Supports the one fixture the hook tests use, tmp_path.
"""

import inspect
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "onescript" / "tests"
sys.path.insert(0, str(TESTS))

import test_hook  # noqa: E402

ok = bad = 0
for name, fn in sorted(vars(test_hook).items()):
    if not name.startswith("test_"):
        continue
    with tempfile.TemporaryDirectory() as d:
        args = [Path(d)] if "tmp_path" in inspect.signature(fn).parameters else []
        try:
            fn(*args)
            ok += 1
        except Exception as e:
            bad += 1
            print("FAIL", name, type(e).__name__, e)
print(sys.version.split()[0], f"{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
