"""Run `ai` as a black box, in a throw-away home, with a fake sandbox.

Everything specific to the implementation of `ai` is here and in
stubs/: the tests only see what it prints, what it asks bwrap for and
what it leaves on disk, so that they apply to any rewrite.  See
README.md.
"""
import functools
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
STUBS = TESTS / "stubs"
AI = Path(os.environ.get("AI_TEST_PROGRAM", TESTS.parent / "ai")).resolve()
# The bash version, kept as a reference, reads a configuration in bash.
IS_BASH = AI.read_bytes().startswith(b"#!/bin/bash")
SHOW_KNOWN_BUGS = bool(os.environ.get("AI_TEST_SHOW_KNOWN_BUGS"))
TIMEOUT = 60

# Names that a text-based implementation can get wrong.  The agent
# chooses the names in the work dir and in its home, so all of these
# must be expected.
NAMES = {
    "plain": "plain",
    "space": "a b",
    "tab": "a\tb",
    "newline": "a\nb",
    "trailing_newline": "ab\n",
    "invalid_utf8": os.fsdecode(b"a\xffb"),
    "utf8": "é€",
    "star": "a*b",
    "question": "a?b",
    "brackets": "a[x]b",
    "backslash": "a\\b",
    "quotes": "a'b\"c",
    "dollar": "$(touch pwned)",
    "percent": "%s%n",
    "leading_dash": "-rf",
    "escape": "a\x1b[31mb",
    "c1_csi": "a\u009b31mb",
    "carriage_return": "a\rb",
    "markup": "<b>a&b",
}


def known_bug(description):
    """Mark a test that fails because of a bug in `ai`, described here.

    The suite stays green, and turns red once the bug is fixed, so the
    mark can't outlive it.  AI_TEST_SHOW_KNOWN_BUGS=1 shows them as
    ordinary failures.
    """
    def mark(test):
        if SHOW_KNOWN_BUGS:
            return test
        return unittest.expectedFailure(test)
    return mark


def for_each_name(known=None, skip=()):
    """Run a test method `test_x(self, name)` once for each of NAMES.

    `known` maps keys of NAMES to the description of a known bug.  Must
    be used on a method of a class decorated with @expand_names.
    """
    def mark(method):
        method._names = (known or {}, skip)
        return method
    return mark


def expand_names(cls):
    for attr, method in list(vars(cls).items()):
        if not hasattr(method, "_names"):
            continue
        known, skip = method._names
        delattr(cls, attr)
        for key, name in NAMES.items():
            if key in skip:
                continue

            def test(self, method=method, name=name):
                method(self, name)
            test = functools.wraps(method)(test)
            test.__name__ = f"{attr}__{key}"
            if key in known:
                test = known_bug(known[key])(test)
            setattr(cls, f"{attr}__{key}", test)
    return cls


def q(s):
    """`s`, str or bytes, as a word for a bash session script."""
    data = s if isinstance(s, bytes) else os.fsencode(s)
    return "$'" + "".join(f"\\x{b:02x}" for b in data) + "'"


def shown_regex(text):
    """A regex for `text` as it must be shown: on one line, with its
    control characters replaced by "?", or tabs left as they are."""
    out = []
    for c in text:
        if c == "\t":
            out.append(r"[\t?]")
        elif ord(c) < 0x20 or 0x7f <= ord(c) < 0xa0:
            out.append(r"\?")
        else:
            out.append(re.escape(c))
    return "".join(out)


class Bwrap:
    """What `ai` asked of the sandbox."""

    def __init__(self, data):
        self.argv = data["argv"]
        self.ops = data["ops"]
        self.piped = data["piped"]
        self.cmd = data["cmd"]
        self.fds = data["fds"]
        self.seccomp_size = data["seccomp_size"]
        self.at_release = data.get("at_release")

    def flags(self):
        return [o[0] for o in self.ops]

    def mounts(self, *flags):
        """(flag, source, target) of the binds, in order."""
        flags = flags or ("--bind", "--ro-bind", "--ro-bind-try", "--bind-try")
        return [tuple(o) for o in self.ops if o[0] in flags]

    def targets(self, *flags):
        return [m[2] for m in self.mounts(*flags)]

    def sources(self, *flags):
        return [m[1] for m in self.mounts(*flags)]

    def env(self):
        return {o[1]: o[2] for o in self.ops if o[0] == "--setenv"}

    def chdir(self):
        return next(o[1] for o in self.ops if o[0] == "--chdir")


class Result:
    def __init__(self, rc, out, err, log, leftovers=()):
        self.rc, self.out, self.err, self.log = rc, out, err, Path(log)
        # The processes of this run that outlived ai.
        self.leftovers = leftovers
        path = self.log / "bwrap.json"
        self.bwrap = Bwrap(json.loads(path.read_text())) if path.exists() else None

    @property
    def text(self):
        return self.out + self.err

    @property
    def notifications(self):
        path = self.log / "notifications"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def logged(self, name):
        return (self.log / name).read_text()

    def __repr__(self):
        return (f"<rc={self.rc}\n--- stdout\n{self.out}\n--- stderr\n{self.err}\n"
                f"--- bwrap\n{self.bwrap and self.bwrap.cmd}>")


class Running:
    """A session of `ai` still running; see AiTestCase.start."""

    def __init__(self, case, leader, master, status, release, out, err, log):
        self.case, self.proc, self.master = case, leader, master
        self.status, self.release = status, release
        self.out, self.err, self.logdir = out, err, log
        self.result = None

    def finished(self, timeout=0):
        return bool(select.select([self.status], [], [], timeout)[0])

    def wait(self, timeout=TIMEOUT):
        if self.result is not None:
            return self.result
        if not self.finished(timeout):
            self.kill()
            self.close()
            raise AssertionError(
                f"ai still running after {timeout}s.\n"
                f"--- stdout\n{self._read(self.out)}\n--- stderr\n{self._read(self.err)}")
        rc = int(os.read(self.status, 32) or b"-9")
        # While its leader lives, so that nothing hangs up what is left.
        leftovers = self._leftovers()
        self.close()
        self.result = Result(rc, self._read(self.out), self._read(self.err),
                             self.logdir, leftovers)
        return self.result

    def close(self):
        """End the session, with what may be left in it."""
        for fd in (self.release, self.status):
            os.close(fd)
        self.proc.wait()
        os.close(self.master)
        self.master = None

    def _leftovers(self, patience=1.0):
        """What still runs with this run's log in its environment, once
        what was just told to stop had the time to."""
        mark = f"AI_TEST_LOG={self.logdir}".encode()
        end = time.monotonic() + patience
        while True:
            found = []
            for pid in filter(str.isdigit, os.listdir("/proc")):
                try:
                    with open(f"/proc/{pid}/environ", "rb") as f:
                        if mark not in f.read().split(b"\0"):
                            continue
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmdline = f.read().replace(b"\0", b" ").decode(errors="replace")
                except OSError:
                    continue
                # Not a zombie, nor the leader.
                if cmdline and int(pid) != self.proc.pid:
                    found.append(cmdline)
            if not found or time.monotonic() > end:
                return found
            time.sleep(0.02)

    def kill(self):
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _read(path):
        return Path(path).read_bytes().decode("utf-8", "surrogateescape")


class AiTestCase(unittest.TestCase):
    """A throw-away $HOME with an empty base home and a project dir."""

    maxDiff = None
    fake_sandbox = True

    def setUp(self):
        root = tempfile.mkdtemp(prefix="ai-test.")
        self.addCleanup(self._remove, root)
        self.root = Path(root).resolve()
        self.home = self.root / "home"
        self.base = self.home / "AI/home"
        self.projects = self.home / "AI/projects"
        self.locks = self.home / "AI/locks"
        self.warnings = self.home / "AI/warnings.log"
        self.work = self.home / "software/proj"
        self.outside = self.root / "outside"
        self.tmp = self.root / "tmp"
        for d in (self.base, self.work, self.outside, self.tmp):
            d.mkdir(parents=True)
        self._runs = 0
        self._running = []
        self.addCleanup(self._stop_all)
        self.write_config()

    @staticmethod
    def _remove(root):
        # Tests leave unreadable directories, as the agent can.
        subprocess.run(["chmod", "-R", "u+rwx", root], stderr=subprocess.DEVNULL)
        shutil.rmtree(root, ignore_errors=True)

    def _stop_all(self):
        for r in self._running:
            if r.master is not None:
                r.kill()
                r.close()

    # --- The configuration: the one place that knows its format. --------

    @property
    def config_path(self):
        return self.home / ("AI/config" if IS_BASH else "AI/config.py")

    def write_config(self, add=None, **settings):
        """Write `ai`'s configuration: set the given settings, and extend
        the lists of `add`, a dictionary, in the language it reads."""
        if self.fake_sandbox:
            settings.setdefault("BWRAP", str(STUBS / "bwrap"))
        render = render_bash if IS_BASH else render_python
        text = "".join(render(name, value, False) for name, value in settings.items())
        text += "".join(render(name, value, True) for name, value in (add or {}).items())
        return self.write_config_text(text)

    def write_config_text(self, text):
        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, errors="surrogateescape")
        path.chmod(0o600)
        return path

    # --- Running ai. ----------------------------------------------------

    def environment(self, extra=None):
        path = os.environ["PATH"]
        if self.fake_sandbox:
            path = f"{STUBS}:{path}"
        env = {
            "HOME": str(self.home),
            "USER": "tester",
            "PATH": path,
            "LANG": "C.UTF-8",
            "TERM": "dumb",
            "TMPDIR": str(self.tmp),
            # Not limited: tests of the limits unset it.
            "AI_IN_SCOPE": "1",
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
        }
        env.update(extra or {})
        return {k: v for k, v in env.items() if v is not None}

    def start(self, *args, cwd=None, answers=(), session=None, env=None):
        """Start `ai ARGS` on a terminal of its own.

        `answers` are typed at its questions; there is an end of file
        after them, so another question gets "no" and nothing hangs.
        `session` is the shell code of the session; see stubs/bwrap.
        """
        self._runs += 1
        log = self.root / f"log/{self._runs}"
        log.mkdir(parents=True)
        out, err = log / "stdout", log / "stderr"
        environ = self.environment(env)
        environ["AI_TEST_LOG"] = str(log)
        if session is not None:
            environ["AI_TEST_SESSION"] = session

        master, slave = os.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        for answer in answers:
            os.write(master, answer.encode() + b"\n")
        os.write(master, b"\x04")

        status_r, status_w = os.pipe()
        release_r, release_w = os.pipe()
        with open(out, "wb") as o, open(err, "wb") as e:
            leader = subprocess.Popen(
                [sys.executable, "-I", str(TESTS / "session_leader.py"),
                 str(status_w), str(release_r), str(AI), *map(str, args)],
                cwd=os.fsencode(str(cwd if cwd is not None else self.work)),
                env=environ, stdin=slave, stdout=o, stderr=e,
                pass_fds=(status_w, release_r),
            )
        for fd in (slave, status_w, release_r):
            os.close(fd)
        running = Running(self, leader, master, status_r, release_w, out, err, log)
        self._running.append(running)
        return running

    def ai(self, *args, **kwargs):
        return self.start(*args, **kwargs).wait()

    def wait_for(self, path, running=None, timeout=20):
        """Wait for the file `path`, which a session creates."""
        end = time.monotonic() + timeout
        while not os.path.lexists(path):
            if running is not None and running.finished():
                self.fail(f"ai ended before creating {path}: {running.wait()!r}")
            if time.monotonic() > end:
                self.fail(f"{path} did not appear")
            time.sleep(0.02)

    # --- What tests have in common. -------------------------------------

    def project_dir(self, work=None):
        escaped = subprocess.run(
            ["systemd-escape", "--path", "--", os.fsencode(str(work or self.work))],
            capture_output=True, check=True).stdout.decode("utf-8", "surrogateescape")
        return self.projects / escaped.strip("\n")

    def git(self, *args, cwd=None):
        env = dict(self.environment(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                   GIT_CONFIG_NOSYSTEM="1")
        p = subprocess.run(["/usr/bin/git", *map(str, args)], cwd=cwd or self.work,
                           env=env, capture_output=True, text=True, errors="surrogateescape")
        if p.returncode:
            self.fail(f"git {args}: {p.stderr}")
        return p.stdout

    def make_repo(self, path=None, commit=True):
        path = Path(path or self.work)
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main", cwd=path)
        if commit:
            (path / "file").write_text("content\n")
            self.git("add", "file", cwd=path)
            self.git("commit", "-q", "-m", "first", cwd=path)
        return path

    def assertOk(self, result):
        self.assertEqual(result.rc, 0, repr(result))

    def assertRefused(self, result, message):
        """`ai` stopped with an explanation, and no sandbox started."""
        self.assertNotEqual(result.rc, 0, repr(result))
        self.assertIn(message, result.err, repr(result))
        self.assertIsNone(result.bwrap, repr(result))

    def assertShown(self, text, value, msg=None):
        """`value` is in `text`, as a name from the agent must be shown."""
        self.assertRegex(text, shown_regex(value), msg)

    def assertSafe(self, text):
        """Nothing in `text` could drive a terminal."""
        bad = re.findall(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", text)
        self.assertEqual(bad, [], f"control characters in: {text!r}")


def render_python(name, value, extend):
    return f"{name} {'+=' if extend else '='} {value!r}\n"


def render_bash(name, value, extend):
    def quote(v):
        return "'" + v.replace("'", "'\\''") + "'"
    if isinstance(value, str):
        return f"{name}={quote(value)}\n"
    words = " ".join(map(quote, value))
    return f"{name}{'+=' if extend else '='}({words})\n"
