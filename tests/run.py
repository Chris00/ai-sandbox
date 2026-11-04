#!/usr/bin/python3
"""Run the tests in parallel, one process per test class.

Each test has its own $HOME, so they don't interfere.  The arguments
select the classes whose name contains one of them.  AI_TEST_JOBS sets
the number of processes (default: the number of CPUs).
"""
import concurrent.futures
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def classes(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from classes(test)
        else:
            yield f"{type(test).__module__}.{type(test).__qualname__}"


def run(name):
    p = subprocess.run([sys.executable, "-m", "unittest", name], cwd=TESTS,
                       capture_output=True, text=True, errors="replace")
    return name, p.returncode, p.stderr


def main():
    sys.path.insert(0, str(TESTS))
    found = sorted(set(classes(unittest.defaultTestLoader.discover(str(TESTS)))))
    found = [c for c in found
             if not sys.argv[1:] or any(a in c for a in sys.argv[1:])]
    jobs = int(os.environ.get("AI_TEST_JOBS", os.cpu_count()))
    totals = dict(tests=0, failures=0, errors=0, skipped=0, expected=0, unexpected=0)
    failed = False
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for name, rc, err in pool.map(run, found):
            m = re.search(r"^Ran (\d+) tests?", err, re.M)
            totals["tests"] += int(m.group(1)) if m else 0
            for key, pattern in (("failures", "(?<!expected )failures"), ("errors", "errors"),
                                 ("skipped", "skipped"), ("expected", "expected failures"),
                                 ("unexpected", "unexpected successes")):
                m = re.search(rf"\b{pattern}=(\d+)", err)
                totals[key] += int(m.group(1)) if m else 0
            if rc:
                failed = True
                print(err)
            print(f"{'FAILED' if rc else 'ok':6}  {name}", flush=True)
    print(", ".join(f"{v} {k}" for k, v in totals.items()))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
