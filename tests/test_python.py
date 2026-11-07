"""What is specific to the Python version: its configuration, above all."""
import os
import subprocess
import sys
import unittest

from harness import AI, IS_BASH, AiTestCase, STUBS


@unittest.skipIf(IS_BASH, "the bash version reads a configuration in bash")
class Configuration(AiTestCase):
    def config(self, text):
        self.write_config_text(f"BWRAP = {str(STUBS / 'bwrap')!r}\n"
                               f"PYTHON = {str(STUBS / 'python3')!r}\n{text}\n")

    def test_it_is_python(self):
        self.config('AI_ENV = [f"N={n}" for n in range(2)]\nREADONLY.append("mine")')
        (self.work / "mine").touch()
        r = self.ai("-w")
        self.assertOk(r)
        self.assertEqual(r.bwrap.env()["N"], "1")
        self.assertIn(str(self.work / "mine"), r.bwrap.sources("--ro-bind"))

    def test_it_knows_the_home(self):
        self.config('FORBID_WORK.append(f"{HOME}/software")')
        self.assertRefused(self.ai("-w"), "FORBID_WORK")

    def test_paths_can_be_path_objects(self):
        self.config('from pathlib import Path\nFORBID_WORK = [Path(HOME) / "software"]')
        self.assertRefused(self.ai("-w"), "FORBID_WORK")

    def test_a_syntax_error_is_refused_with_its_line(self):
        self.config("AI_ENV = [")
        r = self.ai("-w")
        self.assertRefused(r, f"{self.config_path}, line ")
        self.assertNotIn("Traceback", r.err)

    def test_an_error_at_run_time_is_refused_with_its_line(self):
        self.config("\n\n1 / 0")
        r = self.ai("--list")
        self.assertRefused(r, f"{self.config_path}, line 5: ZeroDivisionError")
        self.assertNotIn("Traceback", r.err)

    def test_an_error_on_no_line_is_refused(self):
        self.config("x = " + "-" * 100000 + "1")    # too complex to parse
        r = self.ai("--list")
        self.assertRefused(r, f"{self.config_path}: MemoryError")
        self.assertNotIn("Traceback", r.err)

    def test_settings_of_the_wrong_type_are_refused(self):
        for text, name in (('READONLY = ".envrc"', "READONLY"),    # would be letters
                           ('PRIVATE = [1]', "PRIVATE"),
                           ('AI_ENV = {"A": "1"}', "AI_ENV"),
                           ('PRIVATE_IF = ["x"]', "PRIVATE_IF"),
                           ('AIHOME = 3', "AIHOME"),
                           ('AI_CPUS = 1.5', "AI_CPUS")):
            with self.subTest(text=text):
                self.config(text)
                r = self.ai("-w")
                self.assertRefused(r, f"{name} in your configuration must be a")
                self.assertNotIn("Traceback", r.err)

    def test_a_misspelt_setting_is_refused(self):
        self.config('FORBID_WORKS = [f"{HOME}/software"]\nmine = 1\nPath = None')
        r = self.ai("-w")
        self.assertRefused(r, "no such setting: FORBID_WORKS (")
        self.assertNotIn("mine", r.err)
        self.assertNotIn("Path", r.err)

    def test_an_environment_entry_cannot_smuggle_options(self):
        self.config(r'AI_ENV = ["A=1\0--bind\0/\0/host"]')
        self.assertRefused(self.ai("-w"), "NAME=value")

    def test_a_removed_setting_keeps_its_default(self):
        self.config("del READONLY")
        (self.work / ".envrc").touch()
        r = self.ai("-w")
        self.assertOk(r)
        self.assertIn(str(self.work / ".envrc"), r.bwrap.sources("--ro-bind"))

    def test_limits_in_the_configuration_yield_to_the_environment(self):
        self.config("AI_CPUS = AI_CPUS or 4")
        for env, quota in (({}, "CPUQuota=400%"), (dict(AI_CPUS="2"), "CPUQuota=200%")):
            with self.subTest(env=env):
                r = self.ai("-w", env=dict(env, AI_IN_SCOPE=None))
                self.assertIn(quota, r.logged("systemd-run.args").splitlines())

    def test_the_old_configuration_is_not_silently_dropped(self):
        self.config_path.unlink()
        old = self.home / "AI/config"
        old.write_text(f"BWRAP={STUBS}/bwrap\nFORBID_WORK=(...)\n")
        for args in (["-w"], ["--list"]):
            with self.subTest(args=args):
                r = self.ai(*args)
                self.assertRefused(r, "is in bash, which ai no longer reads")

    def test_both_configuration_files_are_refused_as_work_dirs(self):
        d = self.home / "AI/config"
        d.mkdir()
        self.assertRefused(self.ai("-w", cwd=d), "the sandbox's own files")


@unittest.skipIf(IS_BASH, "about how Python runs")
class Interpreter(AiTestCase):
    def test_nothing_is_imported_from_the_current_directory(self):
        for module in ("seccomp", "json", "subprocess", "re", "signal"):
            (self.work / f"{module}.py").write_text(
                f"open({str(self.outside / 'pwned')!r}, 'w').close()\n")
        env = dict(PYTHONPATH=str(self.work), PYTHONSTARTUP=str(self.work / "re.py"))
        for args in (["-w"], ["--list"], ["--help"]):
            with self.subTest(args=args):
                self.ai(*args, env=env)
                self.assertFalse((self.outside / "pwned").exists())

    def test_an_error_in_a_thread_is_shown_made_safe(self):
        # Nothing makes one happen: load ai as a module and raise one, whose
        # message has a name as it is (OSError's show theirs escaped).
        code = ("import importlib.machinery, importlib.util, sys, threading\n"
                "loader = importlib.machinery.SourceFileLoader('ai', sys.argv[1])\n"
                "spec = importlib.util.spec_from_loader('ai', loader)\n"
                "loader.exec_module(importlib.util.module_from_spec(spec))\n"
                "def fail(): raise ValueError(sys.argv[2])\n"
                "t = threading.Thread(target=fail)\n"
                "t.start()\n"
                "t.join()\n")
        p = subprocess.run([sys.executable, "-I", "-c", code, AI, f"{self.work}/a\x1b[31mb"],
                           capture_output=True, text=True, errors="replace")
        self.assertIn("ai: internal error in a thread:", p.stderr, p)
        self.assertIn(f"{self.work}/a?[31mb", p.stderr, p)
        self.assertNotIn("\x1b", p.stderr, p)

    def test_a_closed_output_is_not_an_error_to_show(self):
        env = dict(self.environment(), AI_TEST_LOG=str(self.outside))
        reader = subprocess.run(
            f"{AI} --help | true; echo $?", shell=True, cwd=self.work, env=env,
            capture_output=True, text=True, executable="/bin/bash")
        self.assertNotIn("Traceback", reader.stderr)

    def test_a_current_directory_that_is_gone(self):
        gone = self.home / "gone"
        gone.mkdir()
        script = f'cd {gone} && rmdir {gone} && exec {AI} "$@"'
        env = dict(self.environment(), AI_TEST_LOG=str(self.outside))
        for args, message in ((["-w"], "Cannot resolve the current directory"),
                              (["--status"], "No project here")):
            with self.subTest(args=args):
                gone.mkdir(exist_ok=True)
                p = subprocess.run(["/bin/bash", "-c", script, "bash", *args],
                                   env=env, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL)
                self.assertIn(message, p.stderr)
                self.assertNotIn("Traceback", p.stderr)

    def test_the_sandbox_gets_only_its_descriptors(self):
        r = self.ai("-w", "--", "tool")
        self.assertOk(r)
        given = {str(fd) for fd in ("0", "1", "2")}
        for flag in ("--info-fd", "--block-fd", "--seccomp", "--args"):
            given.add(r.bwrap.argv[r.bwrap.argv.index(flag) + 1])
        self.assertLessEqual(set(r.bwrap.fds), given | {"3"}, r.bwrap.fds)
        for target in r.bwrap.fds.values():
            for secret in ("/AI/locks/", "/notify"):
                self.assertNotIn(secret, target)

    def test_the_environment_entries_are_not_on_a_file_in_the_tree(self):
        self.write_config(AI_ENV=["TOKEN=hunter2"])
        r = self.ai("-w", session='grep -rs hunter2 "$WORK" "$TMPDIR" && exit 9; exit 0')
        self.assertOk(r)

    def test_the_session_ends_when_a_helper_is_gone(self):
        # Nothing waits for slirp4netns or the watcher at the end.
        r = self.ai("-w", session="true")
        self.assertOk(r)
        self.assertEqual(r.leftovers, [])


if __name__ == "__main__":
    unittest.main()
