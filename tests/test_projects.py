"""ai --status, --list, --promote, --reset and --forget."""
import os
import unittest

from harness import AiTestCase, q


class ProjectCase(AiTestCase):
    def layer(self):
        return self.project_dir() / "home"

    def in_layer(self, rel, content="project\n"):
        path = self.layer() / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def in_base(self, rel, content="base\n"):
        path = self.base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def agent_commit(self, message="agent", branch=None):
        """A commit in the project's clone, as the agent would make it."""
        clone = self.project_dir() / "git"
        if branch:
            self.git("switch", "-q", "-c", branch, cwd=clone)
        self.git("commit", "-q", "--allow-empty", "-m", message, cwd=clone)
        return clone


class Status(ProjectCase):
    def test_no_project(self):
        self.assertRefused(self.ai("--status"), "No project here")

    def test_a_branch_of_the_agent_with_no_valid_commit(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        clone = self.project_dir() / "git/.git"
        (clone / "refs/heads/bad").write_text("not an object name\n")
        r = self.ai("--status")
        self.assertOk(r)
        self.assertNotIn("ai/bad", r.out)

    def test_a_project(self):
        self.assertOk(self.ai("-w"))
        r = self.ai("--status")
        self.assertOk(r)
        self.assertIn("Project: ~/software/proj\n", r.out)
        self.assertIn("Session: none", r.out)

    def test_from_a_subdirectory(self):
        self.assertOk(self.ai("-w"))
        (self.work / "a/b").mkdir(parents=True)
        self.assertIn("Project: ~/software/proj\n", self.ai("--status", cwd=self.work / "a/b").out)

    def test_a_running_session(self):
        hold = 'touch "$AI_TEST_LOG/up"; while [ ! -e "$AI_TEST_LOG/stop" ]; do sleep 0.02; done'
        s = self.start("-w", session=hold)
        self.wait_for(s.logdir / "up", s)
        self.assertIn("Session: running since", self.ai("--status").out)
        self.assertIn("session running", self.ai("--list").out)
        (s.logdir / "stop").touch()
        self.assertOk(s.wait())

    def test_a_repo_with_nothing_to_merge(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.assertIn("nothing left to merge", self.ai("--status").out)

    def test_commits_not_fetched(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.agent_commit()
        r = self.ai("--status")
        self.assertIn("ai/main: new commits, not fetched yet", r.out)

    def test_commits_fetched_and_not_merged(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.agent_commit()
        self.git("fetch", "-q", "ai")
        self.assertIn("ai/main: 1 commit not in your branches", self.ai("--status").out)
        self.agent_commit()
        self.git("fetch", "-q", "ai")
        self.assertIn("ai/main: 2 commits not in your branches", self.ai("--status").out)

    def test_commits_merged(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.agent_commit()
        self.git("fetch", "-q", "ai")
        self.git("merge", "-q", "--ff-only", "ai/main")
        self.assertIn("nothing left to merge", self.ai("--status").out)

    def test_a_repo_gone_from_the_project(self):
        import shutil
        sub = self.make_repo(self.work / "sub")
        self.assertOk(self.ai("-w", answers=["y"]))
        shutil.rmtree(sub)
        self.assertIn("gone from the project", self.ai("--status").out)

    def test_private_copies(self):
        (self.work / "target").mkdir()
        (self.work / "sub/node_modules").mkdir(parents=True)
        self.assertOk(self.ai("-w"))
        r = self.ai("--status")
        self.assertIn("Private copies:", r.out)
        self.assertRegex(r.out, r"  target: \S+\n")
        self.assertRegex(r.out, r"  sub/node_modules: \S+\n")

    def test_unused_private_copies(self):
        (self.work / "target").mkdir()
        self.assertOk(self.ai("-w"))
        (self.work / "target").rmdir()
        self.assertIn("unused: no target in the project", self.ai("--status").out)

    def test_private_copy_a_tool_would_create_is_not_unused(self):
        (self.work / "Cargo.toml").write_text("")
        self.assertOk(self.ai("-w"))
        self.assertNotIn("unused", self.ai("--status").out)

    def test_home_layer(self):
        self.assertOk(self.ai("-w"))
        self.in_layer(".bashrc")
        self.in_layer("notes.txt")
        r = self.ai("--status")
        self.assertIn("changes to the agent's home:", r.out)
        self.assertIn("! .bashrc\n", r.out)
        self.assertIn("  notes.txt\n", r.out)

    def test_status_does_not_run_what_the_agent_left_in_the_clone(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        clone = self.project_dir() / "git"
        self.agent_commit()
        marker = self.outside / "ran"
        evil = self.outside / "evil.sh"
        evil.write_text(f"#!/bin/sh\ntouch {marker}\n")
        evil.chmod(0o755)
        for hook in ("post-checkout", "pre-push", "post-index-change", "reference-transaction",
                     "pre-upload"):
            path = clone / ".git/hooks" / hook
            path.write_text(evil.read_text())
            path.chmod(0o755)
        for key in ("core.fsmonitor", "core.sshCommand", "core.pager", "uploadpack.packObjectsHook",
                    "core.editor", "diff.external"):
            self.git("config", key, str(evil), cwd=clone)
        self.assertOk(self.ai("--status"))
        self.assertFalse(marker.exists(), "--status ran code from the clone")
        self.git("fetch", "-q", "ai")
        self.assertFalse(marker.exists(), "git fetch ran code from the clone")


class List(ProjectCase):
    def test_no_projects(self):
        self.assertIn("No projects yet", self.ai("--list").out)

    def test_projects(self):
        other = self.home / "other"
        other.mkdir()
        self.assertOk(self.ai("-w"))
        self.assertOk(self.ai("-w", cwd=other))
        r = self.ai("--list")
        self.assertRegex(r.out, r"~/software/proj +\S+ +\d{4}-\d\d-\d\d \d\d:\d\d\n")
        self.assertRegex(r.out, r"~/other +\S+ +\d{4}-\d\d-\d\d \d\d:\d\d\n")

    def test_a_long_path_can_be_a_project(self):
        long = self.home / ("é" * 40)
        long.mkdir()
        self.assertOk(self.ai("-w", cwd=long))

    def test_long_paths_alike_are_two_projects(self):
        # Their names are cut: what tells them apart is past the cut.
        one, two = self.home / ("é" * 60 + "1"), self.home / ("é" * 60 + "2")
        for d in (one, two):
            d.mkdir()
            self.assertOk(self.ai("-w", cwd=d))
        self.assertEqual(len(os.listdir(self.projects)), 2)
        r = self.ai("--list")
        self.assertIn(f"~/{one.name} ", r.out)
        self.assertIn(f"~/{two.name} ", r.out)

    def test_the_columns_line_up_with_non_ascii_names(self):
        wide = self.home / "é€é€"
        wide.mkdir()
        self.assertOk(self.ai("-w"))
        self.assertOk(self.ai("-w", cwd=wide))
        lines = self.ai("--list").out.splitlines()
        self.assertEqual(len({len(line) for line in lines}), 1, lines)

    def test_a_project_gone(self):
        self.assertOk(self.ai("-w", cwd=self.outside))
        self.outside.rmdir()
        self.assertIn(f"gone: ai --forget {self.outside}", self.ai("--list", cwd=self.root).out)


class Promote(ProjectCase):
    def setUp(self):
        super().setUp()
        self.assertOk(self.ai("-w"))

    def test_the_contents_shown_cannot_drive_the_terminal(self):
        self.assertOk(self.ai("-w", session=r"""
            printf 'a\033[31mb\302\233c\n' >"$LAYER/evil" """))
        r = self.ai("--promote", "evil", answers=["n"])
        self.assertSafe(r.out)
        self.assertIn("a?[31mb?c", r.out)

    def test_not_in_a_project(self):
        self.assertRefused(self.ai("--promote", ".bashrc", cwd=self.outside), "No project here")

    def test_no_changes(self):
        self.assertRefused(self.ai("--promote", ".bashrc"), "has no changes in this project")

    def test_shows_the_changes_and_makes_them_the_base(self):
        self.in_base(".bashrc")
        path = self.in_layer(".bashrc")
        r = self.ai("--promote", "~/.bashrc", answers=["y"])
        self.assertOk(r)
        self.assertIn("-base", r.out)
        self.assertIn("+project", r.out)
        self.assertEqual((self.base / ".bashrc").read_text(), "project\n")
        self.assertFalse(path.exists(), "the layer must see the base again")

    def test_a_refusal_changes_nothing(self):
        self.in_base(".bashrc")
        path = self.in_layer(".bashrc")
        r = self.ai("--promote", ".bashrc", answers=["n"])
        self.assertNotEqual(r.rc, 0)
        self.assertEqual((self.base / ".bashrc").read_text(), "base\n")
        self.assertTrue(path.exists())

    def test_ways_of_naming_a_file(self):
        for name in (".bashrc", "~/.bashrc", "./.bashrc", f"{self.home}/.bashrc"):
            with self.subTest(name=name):
                self.in_layer(".bashrc")
                self.assertOk(self.ai("--promote", name, answers=["y"]))
                self.assertEqual((self.base / ".bashrc").read_text(), "project\n")

    def test_a_directory(self):
        self.in_layer("d/a")
        self.in_layer("d/sub/b")
        self.assertOk(self.ai("--promote", "d", answers=["y"]))
        self.assertEqual((self.base / "d/a").read_text(), "project\n")
        self.assertEqual((self.base / "d/sub/b").read_text(), "project\n")

    def test_several(self):
        self.in_layer("a")
        self.in_layer("b")
        self.assertOk(self.ai("--promote", "a", "b", answers=["y"]))
        self.assertTrue((self.base / "a").exists() and (self.base / "b").exists())

    def test_all_or_nothing(self):
        self.in_layer("a")
        self.assertRefused(self.ai("--promote", "a", "nothing", answers=["y"]),
                           "has no changes in this project")
        self.assertFalse((self.base / "a").exists())

    def test_a_symlink(self):
        self.layer().mkdir(exist_ok=True)
        (self.layer() / "link").symlink_to("/etc/passwd")
        r = self.ai("--promote", "link", answers=["y"])
        self.assertOk(r)
        self.assertIn("Symlink ~/link → /etc/passwd", r.out)
        self.assertEqual(os.readlink(self.base / "link"), "/etc/passwd")

    def test_a_deletion(self):
        self.in_base("gone")
        os.mknod(self.layer() / "gone", 0o600 | 0o020000, 0)
        r = self.ai("--promote", "gone", answers=["y"])
        self.assertOk(r)
        self.assertIn("Delete ~/gone", r.out)
        self.assertFalse((self.base / "gone").exists())
        self.assertFalse(os.path.lexists(self.layer() / "gone"))

    def test_the_mode_is_kept(self):
        self.in_layer("tool").chmod(0o750)
        self.assertOk(self.ai("--promote", "tool", answers=["y"]))
        self.assertEqual((self.base / "tool").stat().st_mode & 0o777, 0o750)

    def test_outside_the_home(self):
        self.assertRefused(self.ai("--promote", "/etc/passwd"), "is not in the agent's home")

    def test_traversal(self):
        for name in ("../../etc/passwd", "a/../../b", ".", "..", "~/"):
            with self.subTest(name=name):
                self.assertRefused(self.ai("--promote", name), "Not a file of the agent's home")

    def test_a_layer_directory_leading_out(self):
        self.layer().mkdir(exist_ok=True)
        (self.outside / "f").write_text("x")
        (self.layer() / "d").symlink_to(self.outside)
        self.assertRefused(self.ai("--promote", "d/f", answers=["y"]), "leads out of it")

    def test_a_base_directory_leading_out(self):
        self.in_layer("d/f")
        (self.base / "d").symlink_to(self.outside)
        r = self.ai("--promote", "d/f", answers=["y"])
        self.assertRefused(r, "leads out of the base home")
        self.assertEqual(os.listdir(self.outside), [])

    def test_not_written_through_a_symlink_in_the_base(self):
        victim = self.outside / "victim"
        victim.write_text("safe\n")
        (self.base / "f").symlink_to(victim)
        self.in_layer("f")
        self.assertOk(self.ai("--promote", "f", answers=["y"]))
        self.assertEqual(victim.read_text(), "safe\n")
        self.assertFalse((self.base / "f").is_symlink())

    def test_a_named_pipe_is_refused(self):
        self.layer().mkdir(exist_ok=True)
        os.mkfifo(self.layer() / "p")
        self.assertRefused(self.ai("--promote", "p", answers=["y"]),
                           "is not a file, a symlink or a deletion")

    def test_an_opaque_directory_is_refused(self):
        path = self.in_layer("d/f")
        os.setxattr(path.parent, "user.overlay.opaque", b"y")
        self.assertRefused(self.ai("--promote", "d/f", answers=["y"]), "as a whole")
        self.assertRefused(self.ai("--promote", "d", answers=["y"]), "as a whole")

    def test_an_opaque_directory_inside_is_refused(self):
        path = self.in_layer("d/sub/f")
        os.setxattr(path.parent, "user.overlay.opaque", b"y")
        self.assertRefused(self.ai("--promote", "d", answers=["y"]), "as a whole")

    def test_control_characters_in_the_file_are_not_shown(self):
        self.in_layer("f", "evil\x1b[2Jtext\n")
        r = self.ai("--promote", "f", answers=["n"])
        self.assertNotIn("\x1b", r.out)
        self.assertIn("evil?[2Jtext", r.out)

    def test_not_while_a_session_runs(self):
        other = self.home / "other"
        other.mkdir()
        hold = 'touch "$AI_TEST_LOG/up"; while [ ! -e "$AI_TEST_LOG/stop" ]; do sleep 0.02; done'
        s = self.start("-w", cwd=other, session=hold)
        self.wait_for(s.logdir / "up", s)
        self.in_layer("f")
        self.assertRefused(self.ai("--promote", "f", answers=["y"]), "must not change under them")
        (s.logdir / "stop").touch()
        self.assertOk(s.wait())


class Reset(ProjectCase):
    def test_no_project(self):
        self.assertRefused(self.ai("--reset"), "No project here")

    def test_no_changes(self):
        (self.project_dir()).mkdir(parents=True)
        (self.project_dir() / "path").write_text(f"{self.work}\n")
        self.assertRefused(self.ai("--reset"), "has no changes in this project")

    def test_shows_them_and_deletes_them(self):
        self.assertOk(self.ai("-w"))
        self.in_layer(".bashrc")
        r = self.ai("--reset", answers=["y"])
        self.assertOk(r)
        self.assertIn("! .bashrc", r.out)
        self.assertFalse(self.layer().exists())
        self.assertFalse((self.project_dir() / "home.work").exists())
        self.assertTrue((self.project_dir() / "path").exists())

    def test_a_refusal_changes_nothing(self):
        self.assertOk(self.ai("-w"))
        path = self.in_layer(".bashrc")
        self.assertNotEqual(self.ai("--reset", answers=["n"]).rc, 0)
        self.assertTrue(path.exists())

    def test_unreadable_directories_are_deleted(self):
        self.assertOk(self.ai("-w"))
        path = self.in_layer("locked/f")
        path.parent.chmod(0)
        self.assertOk(self.ai("--reset", answers=["y"]))
        self.assertFalse(self.layer().exists())

    def test_not_during_a_session(self):
        hold = 'touch "$AI_TEST_LOG/up"; while [ ! -e "$AI_TEST_LOG/stop" ]; do sleep 0.02; done'
        s = self.start("-w", session=hold)
        self.wait_for(s.logdir / "up", s)
        self.assertRefused(self.ai("--reset", answers=["y"]), "A session is running")
        (s.logdir / "stop").touch()
        self.assertOk(s.wait())

    def test_symlinks_are_not_followed(self):
        self.assertOk(self.ai("-w"))
        (self.outside / "precious").write_text("x")
        self.layer().mkdir(exist_ok=True)
        (self.layer() / "link").symlink_to(self.outside)
        self.assertOk(self.ai("--reset", answers=["y"]))
        self.assertTrue((self.outside / "precious").exists())


class Forget(ProjectCase):
    def test_no_hook_of_your_repo_runs_when_the_fetched_refs_are_deleted(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.git("fetch", "-q", "ai")
        self.assertIn("ai/main", self.git("branch", "-r"))
        hook = self.work / ".git/hooks/reference-transaction"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch {self.outside}/pwned\n")
        hook.chmod(0o755)
        r = self.ai("--forget", answers=["y"])
        self.assertOk(r)
        self.assertIn("ai remote removed", r.out)
        self.assertNotIn("ai/", self.git("branch", "-r"))
        self.assertFalse((self.outside / "pwned").exists())

    def test_no_project(self):
        self.assertRefused(self.ai("--forget"), "No project here")

    def test_not_a_project(self):
        self.assertRefused(self.ai("--forget", self.outside), "is not a project")

    def test_deletes_everything(self):
        self.make_repo()
        (self.work / "target").mkdir()
        self.assertOk(self.ai("-w", answers=["y"]))
        r = self.ai("--forget", answers=["y"])
        self.assertOk(r)
        self.assertFalse(self.project_dir().exists())
        self.assertIn("ai remote removed", r.out)
        self.assertNotIn("ai", self.git("remote").split())
        self.assertEqual(os.listdir(self.work / "target"), [])

    def test_no_hook_of_your_repo_runs(self):
        # The agent could have changed what is in a repo it created.  Removing
        # the remote changes refs, which is what a hook can watch.
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.agent_commit()
        self.git("fetch", "-q", "ai")
        marker = self.outside / "ran"
        hook = self.work / ".git/hooks/reference-transaction"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        self.assertOk(self.ai("--forget", answers=["y"]))
        self.assertNotIn("ai", self.git("remote").split())
        self.assertFalse(marker.exists(), "--forget ran a hook of your repo")

    def test_a_refusal_changes_nothing(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.assertNotEqual(self.ai("--forget", answers=["n"]).rc, 0)
        self.assertTrue(self.project_dir().exists())
        self.assertIn("ai", self.git("remote").split())

    def test_warns_about_commits_only_in_the_clone(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.agent_commit()
        r = self.ai("--forget", answers=["n"])
        self.assertIn("deleting the clones and the ai", r.err)

    def test_no_warning_when_all_is_merged(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        r = self.ai("--forget", answers=["n"])
        self.assertNotIn("deleting the clones and the ai", r.err)

    def test_a_project_gone(self):
        self.assertOk(self.ai("-w", cwd=self.outside))
        project = self.project_dir(self.outside)
        self.outside.rmdir()
        self.assertOk(self.ai("--forget", self.outside, answers=["y"], cwd=self.root))
        self.assertFalse(project.exists())

    def test_only_the_remote_leading_to_this_project(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        self.git("config", "remote.ai.url", str(self.outside / "mine"))
        self.assertOk(self.ai("--forget", answers=["y"]))
        self.assertIn("ai", self.git("remote").split())

    def test_a_tampered_config_is_not_written_through(self):
        self.make_repo()
        self.assertOk(self.ai("-w", answers=["y"]))
        config = self.work / ".git/config"
        target = self.outside / "config"
        target.write_text(config.read_text())
        config.unlink()
        config.symlink_to(target)
        before = target.read_text()
        r = self.ai("--forget", answers=["y"])
        self.assertNotEqual(r.rc, 0)
        self.assertIn("is not a plain file", r.err)
        self.assertEqual(target.read_text(), before)

    def test_symlinks_and_unreadable_directories(self):
        self.assertOk(self.ai("-w"))
        (self.outside / "precious").write_text("x")
        path = self.in_layer("locked/f")
        (self.layer() / "link").symlink_to(self.outside)
        path.parent.chmod(0)
        self.assertOk(self.ai("--forget", answers=["y"]))
        self.assertFalse(self.project_dir().exists())
        self.assertTrue((self.outside / "precious").exists())

    def test_not_during_a_session(self):
        hold = 'touch "$AI_TEST_LOG/up"; while [ ! -e "$AI_TEST_LOG/stop" ]; do sleep 0.02; done'
        s = self.start("-w", session=hold)
        self.wait_for(s.logdir / "up", s)
        self.assertRefused(self.ai("--forget", answers=["y"]), "A session is running")
        (s.logdir / "stop").touch()
        self.assertOk(s.wait())


if __name__ == "__main__":
    unittest.main()
