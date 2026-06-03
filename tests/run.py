#!/usr/bin/env python3
"""Minimal test runner for environments without pytest installed."""

import os
import sys
import traceback

# Allow ``python3 tests/run.py`` as well as ``python3 -m tests.run``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tests.test_pandora as suite


def main() -> int:
    fns = [getattr(suite, n) for n in dir(suite) if n.startswith("test_")]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
