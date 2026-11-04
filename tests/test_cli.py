"""The command line, the configuration and the refusals."""
import os
import unittest

from harness import AiTestCase


class Options(AiTestCase):
    def test_help(self):
        for opt in ("-h", "--help"):
            r = self.ai(opt)
            self.assertOk(r)
            self.assertIn("Usage: ai", r.out)
            self.assertIsNone(r.bwrap)

    def test_help_wins_over_other_options(self):
        r = self.ai("-w", "--bogus", "--help")
        self.assertOk(r)
        self.assertIn("Usage: ai", r.out)

    def test_help_after_the_separator_belongs_to_the_command(self):
        r = self.ai("-w", "--", "tool", "--help")
        self.assertOk(r)
        self.assertEqual(r.bwrap.cmd, ["tool", "--help"])

    def test_action_after_the_separator_belongs_to_the_command(self):
        r = self.ai("-w", "--", "tool", "--list")
        self.assertOk(r)
        self.assertEqual(r.bwrap.cmd, ["tool", "--list"])

    def test_unknown_option(self):
        self.assertRefused(self.ai("--bogus"), "Unknown option --bogus")

    def test_stray_argument(self):
        self.assertRefused(self.ai("claude"), 'Unexpected "claude"')

    def test_nothing_after_the_separator(self):
        self.assertRefused(self.ai("-w", "--"), 'Nothing after "--"')

    def test_read_only_without_pattern(self):
        self.assertRefused(self.ai("-w", "--ro"), "--ro needs at least one pattern")
        self.assertRefused(self.ai("-w", "--ro", "--ssh"), "--ro needs at least one pattern")
        self.assertRefused(self.ai("-w", "--read-only", "--"),
                           "--read-only needs at least one pattern")

    def test_read_only_needs_a_work_dir(self):
        self.assertRefused(self.ai("--ro", "x"), "--ro needs -w")

    def test_promote_needs_paths(self):
        self.assertRefused(self.ai("--promote"), "--promote needs the paths")

    def test_actions_take_no_other_option(self):
        for action in ("--status", "--reset", "--list"):
            for other in (["-w"], ["--ssh"], ["--ro=x"], ["--", "cmd"]):
                with self.subTest(action=action, other=other):
                    self.assertRefused(self.ai(action, *other),
                                       f"{action} takes no other option")

    def test_a_command_is_passed_verbatim(self):
        cmd = ["tool", "a b", "--ssh", "-w", "", "*", "x\ny", "$(z)", "--"]
        r = self.ai("-w", "--", *cmd)
        self.assertOk(r)
        self.assertEqual(r.bwrap.cmd, cmd)

    def test_default_command_is_a_shell_with_job_control(self):
        r = self.ai("-w")
        self.assertOk(r)
        self.assertEqual(r.bwrap.cmd[0], "script")
        self.assertIn("bash -i", r.bwrap.cmd)

    def test_exit_status_of_the_session(self):
        self.assertEqual(self.ai("-w", session="exit 42").rc, 42)
        self.assertEqual(self.ai(session="exit 7").rc, 7)


class Limits(AiTestCase):
    """The scope with the resource limits, from the outside."""

    def limits(self, **env):
        r = self.ai("-w", "--", "tool", "x y", env=dict(AI_IN_SCOPE=None, **env))
        self.assertOk(r)
        return r, r.logged("systemd-run.args").splitlines()

    def test_the_whole_session_runs_in_a_limited_scope(self):
        r, args = self.limits(AI_CPUS="3", AI_TASKS="99", AI_MEMORY="1G", AI_SWAP="2G")
        self.assertIsNone(r.bwrap, "the sandbox started outside the scope")
        for prop in ("MemoryMax=1G", "MemorySwapMax=2G", "CPUQuota=300%", "TasksMax=99"):
            self.assertIn(prop, args)
        # ai again, with the same arguments, in the scope.
        sep = args.index("--")
        self.assertEqual(args[sep + 1:][1:], ["-w", "--", "tool", "x y"])
        self.assertEqual(os.path.basename(args[sep + 1]), "ai")

    def test_defaults(self):
        _, args = self.limits()
        self.assertIn("MemoryMax=50%", args)
        self.assertIn("TasksMax=1024", args)
        cpus = (os.cpu_count() + 1) // 2
        self.assertIn(f"CPUQuota={cpus * 100}%", args)

    def test_without_systemd_it_says_so_and_runs(self):
        r = self.ai("-w", env=dict(AI_IN_SCOPE=None, AI_TEST_NO_SYSTEMD="1"))
        self.assertOk(r)
        self.assertIn("WITHOUT resource limits", r.err)
        self.assertIsNotNone(r.bwrap)

    def test_actions_need_no_scope(self):
        r = self.ai("--list", env=dict(AI_IN_SCOPE=None))
        self.assertOk(r)
        self.assertFalse((r.log / "systemd-run.args").exists())

    def test_invalid_limits(self):
        for name in ("AI_CPUS", "AI_TASKS"):
            for value in ("0", "-1", "x", "1.5", "1 2", "$(id)"):
                with self.subTest(name=name, value=value):
                    self.assertRefused(self.ai("-w", env={name: value}),
                                       "must be positive integers")


class Config(AiTestCase):
    def test_writable_by_others_is_refused(self):
        for mode in (0o620, 0o602, 0o666):
            with self.subTest(mode=oct(mode)):
                path = self.write_config()
                path.chmod(mode)
                self.assertRefused(self.ai("-w"), "writable by you only")

    def test_writable_by_others_is_refused_even_for_actions(self):
        self.write_config().chmod(0o666)
        self.assertRefused(self.ai("--list"), "writable by you only")

    def test_a_symlink_to_a_writable_file_is_refused(self):
        target = self.outside / "config"
        target.write_text(self.write_config().read_text())
        target.chmod(0o666)
        path = self.home / "AI/config"
        path.unlink()
        path.symlink_to(target)
        self.assertRefused(self.ai("-w"), "writable by you only")

    def test_it_is_optional(self):
        # Only the fake sandbox is missing, which the checks report.
        (self.home / "AI/config").unlink()
        r = self.ai("--list")
        self.assertOk(r)

    def test_invalid_environment_entries(self):
        for bad in ("NOVALUE", "1BAD=x", "A-B=x", "=x", ""):
            with self.subTest(entry=bad):
                self.write_config(f"AI_ENV=({bad!r})")
                self.assertRefused(self.ai("-w"), "NAME=value")

    def test_an_invalid_entry_does_not_show_its_value(self):
        self.write_config("AI_ENV=('1BAD=hunter2')")
        r = self.ai("-w")
        self.assertRefused(r, "NAME=value")
        self.assertNotIn("hunter2", r.text)

    def test_lists_can_be_extended(self):
        self.write_config("READONLY+=(myconfig)", "PRIVATE+=(_build)")
        (self.work / "myconfig").write_text("x")
        (self.work / "_build").mkdir()
        r = self.ai("-w")
        self.assertOk(r)
        self.assertIn(str(self.work / "myconfig"), r.bwrap.sources("--ro-bind"))
        targets = r.bwrap.targets("--bind")
        self.assertTrue(any(t.endswith("/_build") for t in targets), targets)


class Checks(AiTestCase):
    def test_base_home_is_needed(self):
        self.base.rmdir()
        self.assertRefused(self.ai("-w"), "Create the directory")

    def test_bwrap_is_needed(self):
        self.write_config(f"BWRAP={self.root}/none")
        self.assertRefused(self.ai("-w"), "bubblewrap is not installed")

    def test_bwrap_must_know_overlays(self):
        old = self.outside / "bwrap"
        old.write_text("#!/bin/sh\necho usage\n")
        old.chmod(0o755)
        self.write_config(f"BWRAP={old}")
        self.assertRefused(self.ai("-w"), "bubblewrap is too old")

    def test_ssh_needs_its_key(self):
        self.assertRefused(self.ai("--ssh"), "create the agent's key first")


class Refused(AiTestCase):
    """Directories that must never be exposed to the agent."""

    def refused(self, cwd, message):
        cwd.mkdir(parents=True, exist_ok=True)
        self.assertRefused(self.ai("-w", cwd=cwd), message)

    def test_the_root(self):
        self.assertRefused(self.ai("-w", cwd="/"), "Refusing to expose the whole file system")

    def test_the_home_and_what_contains_it(self):
        self.refused(self.home, "your whole home")
        self.refused(self.root, "your whole home")

    def test_hidden_directories_of_the_home(self):
        self.refused(self.home / ".config", "configuration files")
        self.refused(self.home / ".config/app", "configuration files")
        self.refused(self.home / ".ssh", "configuration files")

    def test_a_home_that_is_a_symlink(self):
        link = self.root / "link"
        link.symlink_to(self.home)
        (self.home / ".config").mkdir()
        self.assertRefused(self.ai("-w", cwd=self.home / ".config",
                                   env={"HOME": str(link)}), "configuration files")

    def test_a_symlink_into_a_forbidden_directory(self):
        (self.home / ".ssh").mkdir()
        link = self.home / "innocent"
        link.symlink_to(self.home / ".ssh")
        self.assertRefused(self.ai("-w", cwd=link), "configuration files")

    def test_ai_own_files(self):
        for d in (self.home / "AI", self.base, self.base / "sub", self.projects,
                  self.projects / "sub", self.locks):
            with self.subTest(dir=d):
                self.refused(d, "the sandbox's own files")

    def test_ai_own_files_moved_by_the_configuration(self):
        moved = self.home / "elsewhere/projects"
        self.write_config(f'PROJECTS="{moved}"')
        self.refused(moved / "x", "the sandbox's own files")
        self.refused(moved.parent, "the sandbox's own files")

    def test_forbidden_directories(self):
        forbidden = self.home / "Personal"
        self.refused(forbidden, "FORBID_WORK")
        self.refused(forbidden / "sub", "FORBID_WORK")

    def test_a_directory_containing_a_forbidden_one(self):
        self.write_config(f'FORBID_WORK=("{self.home}/a/secret")')
        self.refused(self.home / "a", "FORBID_WORK")

    def test_the_directory_of_the_ssh_key_moved_by_the_configuration(self):
        key = self.home / "keys/ai_key"
        self.write_config(f'AI_SSH_KEY="{key}"')
        self.refused(key.parent, "the sandbox's own files")

    def test_nothing_is_kept_after_a_refusal(self):
        self.refused(self.home / ".config", "configuration files")
        self.assertFalse(self.projects.exists() and list(self.projects.iterdir()))


class Overlapping(AiTestCase):
    def test_a_project_inside_a_project(self):
        self.assertOk(self.ai("-w"))
        sub = self.work / "sub"
        sub.mkdir()
        self.assertRefused(self.ai("-w", cwd=sub), "inside the project")

    def test_a_project_around_a_project(self):
        self.assertOk(self.ai("-w"))
        self.assertRefused(self.ai("-w", cwd=self.work.parent), "contains the project")

    def test_a_sibling_with_the_same_prefix_is_not_inside(self):
        self.assertOk(self.ai("-w"))
        other = self.work.parent / (self.work.name + "2")
        other.mkdir()
        self.assertOk(self.ai("-w", cwd=other))

    def test_glob_characters_in_a_recorded_project_are_literal(self):
        star = self.home / "a*"
        star.mkdir()
        self.assertOk(self.ai("-w", cwd=star))
        other = self.home / "abc"
        other.mkdir()
        self.assertOk(self.ai("-w", cwd=other))


class Concurrent(AiTestCase):
    """Sessions hold locks until they end, however they end."""

    HOLD = 'touch "$AI_TEST_LOG/started"; while [ ! -e "$AI_TEST_LOG/stop" ]; do sleep 0.02; done'

    def hold(self, cwd=None):
        running = self.start(*(["-w"] if cwd else []), cwd=cwd or self.work, session=self.HOLD)
        self.wait_for(running.logdir / "started", running)
        self.addCleanup(lambda: running.result or running.kill())
        return running

    def release(self, running):
        (running.logdir / "stop").touch()
        self.assertOk(running.wait())

    def test_same_directory(self):
        s = self.hold(self.work)
        self.assertRefused(self.ai("-w"), "A session is running in")
        self.release(s)
        self.assertOk(self.ai("-w"))

    def test_inside_a_running_session(self):
        s = self.hold(self.work)
        sub = self.work / "sub"
        sub.mkdir()
        self.assertRefused(self.ai("-w", cwd=sub), "Another session works in")
        self.release(s)

    def test_around_a_running_session(self):
        s = self.hold(self.work)
        self.assertRefused(self.ai("-w", cwd=self.work.parent), "Another session works in")
        self.release(s)

    def test_two_projects_at_once(self):
        other = self.home / "other"
        other.mkdir()
        s = self.hold(self.work)
        self.assertOk(self.ai("-w", cwd=other))
        self.release(s)

    def test_base_home_excludes_project_sessions(self):
        s = self.hold(self.work)
        self.assertRefused(self.ai(), "must not change under them")
        self.release(s)
        self.assertOk(self.ai())

    def test_project_sessions_exclude_the_base_home(self):
        s = self.hold()
        self.assertRefused(self.ai("-w"), "end it first")
        self.release(s)
        self.assertOk(self.ai("-w"))

    def test_locks_die_with_a_killed_session(self):
        s = self.hold(self.work)
        s.kill()
        s.proc.wait()
        # kill: the session's process group, so the locks' holders too.
        self.assertOk(self.ai("-w"))

    def test_the_sandbox_does_not_inherit_the_locks(self):
        r = self.ai("-w")
        leaked = [t for t in r.bwrap.fds.values() if str(self.locks) in t]
        self.assertEqual(leaked, [])

    def test_helpers_do_not_inherit_the_locks(self):
        # The session is gone but a helper might not be: the next session
        # must start right away.
        self.assertOk(self.ai("-w", session="(sleep 30 &) >/dev/null 2>&1"))
        self.assertOk(self.ai("-w"))


if __name__ == "__main__":
    unittest.main()
