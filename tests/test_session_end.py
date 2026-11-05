"""How a session ends, what it shows on a terminal, and in which locale."""
import json
import os
import signal
import subprocess
import unittest

from harness import AI, IS_BASH, AiTestCase, q

HOLD = ('touch "$AI_TEST_LOG/started"; '
        'while [ ! -e "$AI_TEST_LOG/stop" ]; do sleep 0.02; done')


class Signals(AiTestCase):
    def hold(self, session=HOLD):
        running = self.start("-w", session=session)
        self.wait_for(running.logdir / "started", running)
        self.wait_for(running.logdir / "bwrap.json", running)
        data = json.loads((running.logdir / "bwrap.json").read_text())
        return running, (data["ppid"], data["pid"])

    def stop(self, sig, code):
        running, (ai, bwrap) = self.hold()
        (self.work / ".envrc").touch()          # for the report
        # A terminal's signal reaches both.  The session itself runs on, as
        # in the real sandbox it dies with bwrap: tell it to.
        os.kill(ai, sig)
        os.kill(bwrap, sig)
        (running.logdir / "stop").touch()
        r = running.wait()
        self.assertEqual(r.rc, code, repr(r))
        self.assertIn(".envrc", r.err)
        self.assertIn(".envrc", self.warnings.read_text())
        self.assertEqual(os.listdir(self.tmp), [])
        self.assertEqual(r.leftovers, [])

    def test_hangup(self):
        self.stop(signal.SIGHUP, 129)

    def test_interrupt(self):
        self.stop(signal.SIGINT, 130)

    def test_terminate(self):
        self.stop(signal.SIGTERM, 143)

    def test_a_sandbox_killed_by_a_signal(self):
        r = self.ai("-w", session="kill -KILL $PPID; sleep 0.1")
        self.assertEqual(r.rc, 128 + signal.SIGKILL, repr(r))

    def test_the_locks_are_free_after_a_signal(self):
        self.stop(signal.SIGTERM, 143)
        self.assertOk(self.ai("-w"))

    @unittest.skipIf(IS_BASH, "bash runs its traps once bwrap has ended")
    def test_a_signal_to_ai_alone_stops_the_sandbox(self):
        # bwrap ends, and the session with it, as --die-with-parent would.
        running, (ai, bwrap) = self.hold()
        os.kill(ai, signal.SIGTERM)
        self.assertTrue(running.finished(10))
        stopped = not os.path.exists(f"/proc/{bwrap}")
        (running.logdir / "stop").touch()
        r = running.wait()
        self.assertEqual(r.rc, 143, repr(r))
        self.assertTrue(stopped, "bwrap outlived ai")


class Terminal(AiTestCase):
    RED = "\x1b[31m"

    def on_terminal(self, *args):
        """Run ai with its standard output on a terminal."""
        command = " ".join(q(str(a)) for a in (AI, *args))
        # script runs the command with $SHELL, and q quotes for bash.
        env = self.environment(dict(AI_TEST_LOG=str(self.outside), SHELL="/bin/bash"))
        return subprocess.run(["script", "-qefc", command, "/dev/null"],
                              cwd=self.work, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace")

    def test_names_are_in_red_on_a_terminal(self):
        (self.work / ".envrc").touch()
        p = self.on_terminal("-w")
        self.assertIn(f"{self.RED}./.envrc\x1b[0m", p.stdout, p)

    def test_no_colour_otherwise(self):
        (self.work / ".envrc").touch()
        r = self.ai("-w")
        self.assertOk(r)
        self.assertIn("Read-only in the sandbox: ./.envrc", r.err)
        self.assertNotIn("\x1b", r.text)


class Mounts(AiTestCase):
    def test_a_file_system_inside_the_work_dir_must_be_confirmed(self):
        env = dict(AI_TEST_MOUNTS=f"/\n{self.work}/disk\n{self.work}2")
        self.assertRefused(self.ai("-w", env=env), "File systems mounted inside the work dir")
        r = self.ai("-w", env=env, answers=["y"])
        self.assertOk(r)
        self.assertIn(f"  {self.work}/disk\n", r.err)
        self.assertNotIn(f"{self.work}2", r.err)

    def test_others_are_not_shown(self):
        r = self.ai("-w", env=dict(AI_TEST_MOUNTS=f"/\n{self.home}/other"))
        self.assertOk(r)
        self.assertNotIn("File systems", r.err)

    def test_names_escaped_by_findmnt_are_decoded(self):
        # findmnt -r writes each byte that isn't printable ASCII, the space
        # and "\" included, as \xHH.
        work = self.home / "my \\x20 é"
        work.mkdir()
        disk = os.fsencode(f"{work}/disk")
        raw = "".join(chr(b) if 0x21 <= b <= 0x7e and b != 0x5c else f"\\x{b:02x}"
                      for b in disk)
        env = dict(AI_TEST_MOUNTS=f"/\n{raw}")
        self.assertRefused(self.ai("-w", cwd=work, env=env),
                           "File systems mounted inside the work dir")
        r = self.ai("-w", cwd=work, env=env, answers=["y"])
        self.assertOk(r)
        self.assertIn(f"  {work}/disk\n", r.err)


class Log(AiTestCase):
    def test_the_report_is_shown_even_if_the_log_is_not_writable(self):
        self.warnings.mkdir()
        r = self.ai("-w", session='touch "$WORK/.npmrc"')
        self.assertIn(f"{self.work}/.npmrc\n", r.err)
        self.assertIn("warnings.log", r.err)


class Locale(AiTestCase):
    """Names are bytes, whatever the locale says."""

    NAME = os.fsdecode("é€".encode() + b"\xff")

    def test_names_in_every_locale(self):
        for env in (dict(LANG="C"), dict(LANG="C.UTF-8"), dict(LANG="", LC_ALL="POSIX")):
            with self.subTest(env=env):
                d = self.home / (self.NAME + env["LANG"])
                d.mkdir()
                r = self.ai("-w", cwd=d, env=env, session='touch "$WORK"/.envrc')
                self.assertOk(r)
                self.assertTrue((d / ".envrc").exists())
                self.assertIn(self.NAME, r.err)


class WorkDirNames(AiTestCase):
    """A project may have the name of a tool file."""

    @unittest.skipIf(IS_BASH, "bash takes the work dir itself for the tool file, and "
                              "protects nothing in it")
    def test_a_project_named_like_a_private_directory(self):
        d = self.home / "target"
        d.mkdir()
        (d / ".envrc").touch()
        r = self.ai("-w", cwd=d, session='touch "$WORK/new/.npmrc" 2>/dev/null; '
                                         'mkdir "$WORK/new"; touch "$WORK/new/.npmrc"')
        self.assertOk(r)
        self.assertIn(f"{d}/.envrc", r.bwrap.sources("--ro-bind"))
        self.assertIn(f"{d}/new/.npmrc", r.err)


if __name__ == "__main__":
    unittest.main()
