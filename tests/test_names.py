"""Hostile names, everywhere `ai` handles or shows them.

The agent chooses the names in the work dir and in its home.  Each test
runs for each of harness.NAMES.  For each name, `ai` must find the file,
act on it and not be fooled by it, and show it on one line, its control
characters replaced.
"""
import os
import unittest

from harness import IS_BASH, AiTestCase, expand_names, for_each_name, q

SETTLE = "sleep 0.3"
MOUNT = "/work/software/proj"


@expand_names
class ExitReport(AiTestCase):
    @for_each_name()
    def test_new_symlink(self, name):
        r = self.ai("-w", session=f'ln -s /etc/passwd "$WORK"/{q(name)}')
        self.assertShown(r.err, f"./{name} → /etc/passwd")
        self.assertSafe(r.err)

    @for_each_name()
    def test_symlink_target(self, name):
        r = self.ai("-w", session=f'ln -s /x/{q(name)} "$WORK/link"')
        self.assertShown(r.err, f"./link → /x/{name}")
        self.assertSafe(r.err)

    @for_each_name()
    def test_new_tool_file_in_a_directory(self, name):
        d = self.work / name
        d.mkdir()
        r = self.ai("-w", session=f'touch "$WORK"/{q(name)}/.envrc')
        self.assertShown(r.err, f"{self.work}/{name}/.envrc")
        self.assertSafe(r.err)

    @for_each_name()
    def test_new_tool_file_in_a_new_directory(self, name):
        r = self.ai("-w", session=f'mkdir "$WORK"/{q(name)}; touch "$WORK"/{q(name)}/.npmrc')
        self.assertShown(r.err, f"{self.work}/{name}/.npmrc")
        self.assertSafe(r.err)

    @for_each_name()
    def test_it_does_not_hide_the_other_tool_files(self, name):
        (self.work / name).mkdir()
        r = self.ai("-w", session=f'touch "$WORK/.envrc" "$WORK"/{q(name)}/.npmrc')
        self.assertIn(f"{self.work}/.envrc\n", r.err)
        self.assertIn(f"{self.work}/.envrc", self.warnings.read_bytes().decode("utf-8", "replace"))

    @for_each_name()
    def test_it_does_not_hide_the_new_repos(self, name):
        r = self.ai("-w", session=f'git init -q "$WORK/new"; mkdir "$WORK"/{q(name)}; touch "$WORK"/{q(name)}/.npmrc')
        self.assertIn(f"{self.work}/new/.git\n", r.err)

    @for_each_name()
    def test_the_log(self, name):
        (self.work / name).mkdir()
        self.ai("-w", session=f'touch "$WORK"/{q(name)}/.envrc')
        log = self.warnings.read_bytes().decode("utf-8", "surrogateescape")
        self.assertShown(log, f"{self.work}/{name}/.envrc")
        self.assertSafe(log)

    @for_each_name()
    def test_home_change(self, name):
        r = self.ai("-w", session=f'touch "$LAYER"/{q(name)}')
        self.assertShown(r.err, f"  {name}")
        self.assertSafe(r.err)

    @for_each_name()
    def test_home_change_in_a_trigger_directory(self, name):
        r = self.ai("-w", session=f'mkdir -p "$LAYER/.local/bin"; touch "$LAYER/.local/bin"/{q(name)}')
        self.assertShown(r.err, f"! .local/bin/{name}")
        self.assertSafe(r.err)

    @for_each_name()
    def test_home_directory_name_in_a_group(self, name):
        r = self.ai("-w", session=f'd="$LAYER/.claude/skills"/{q(name)}; mkdir -p "$d"; touch "$d/a" "$d/b"')
        self.assertShown(r.err, f"! .claude/skills/{name}/ (2 files)")
        self.assertSafe(r.err)

    @for_each_name()
    def test_base_home_trigger(self, name):
        (self.base / ".local/bin").mkdir(parents=True)
        r = self.ai(session=f'touch "$BASE/.local/bin"/{q(name)}')
        self.assertShown(r.err, f"! .local/bin/{name}")
        self.assertSafe(r.err)


@expand_names
class BeforeTheSession(AiTestCase):
    """The warnings that you read to decide whether to go on."""

    @for_each_name()
    def test_hard_link(self, name):
        (self.outside / "f").write_text("x")
        os.link(self.outside / "f", self.work / name)
        r = self.ai("-w")
        self.assertRefused(r, "Hard-linked outside")
        self.assertShown(r.err, f"{self.work}/{name}")
        self.assertSafe(r.err)

    @for_each_name()
    def test_named_pipe(self, name):
        os.mkfifo(self.work / name)
        r = self.ai("-w")
        self.assertRefused(r, "named pipes")
        self.assertShown(r.err, f"{self.work}/{name}")
        self.assertSafe(r.err)

    @for_each_name()
    def test_tool_file_message(self, name):
        (self.work / name).mkdir()
        (self.work / name / ".envrc").write_text("x")
        r = self.ai("-w")
        self.assertOk(r)
        self.assertShown(r.err, f"./{name}/.envrc")
        self.assertSafe(r.err)

    @for_each_name()
    def test_symlink_skipped_by_read_only(self, name):
        (self.work / name).symlink_to("/etc/passwd")
        r = self.ai("-w", "--ro", "*")
        self.assertShown(r.err, f"./{name}")
        self.assertSafe(r.err)

    @for_each_name()
    def test_new_repo(self, name):
        self.make_repo(self.work / name)
        r = self.ai("-w", answers=["n"])
        self.assertShown(r.err + r.out, f"./{name}")
        self.assertSafe(r.err + r.out)

    @for_each_name()
    def test_git_link(self, name):
        d = self.work / name
        d.mkdir()
        (d / ".git").write_text(f"gitdir: {self.work}/data\n")
        r = self.ai("-w", answers=["n"])
        self.assertShown(r.err, f"./{name}/.git")
        self.assertSafe(r.err)


@expand_names
class Mounts(AiTestCase):
    """The right file is protected, whatever its name."""

    @for_each_name()
    def test_read_only_tool_file(self, name):
        (self.work / name).mkdir()
        (self.work / name / ".envrc").write_text("x")
        r = self.ai("-w")
        self.assertIn(("--ro-bind", f"{self.work}/{name}/.envrc", f"{MOUNT}/{name}/.envrc"),
                      r.bwrap.mounts())

    @for_each_name()
    def test_private_directory(self, name):
        (self.work / name / "target").mkdir(parents=True)
        r = self.ai("-w")
        shadow = self.project_dir() / "shadow"
        self.assertIn(("--bind", f"{shadow}/{name}/target", f"{MOUNT}/{name}/target"),
                      r.bwrap.mounts())

    @for_each_name()
    def test_nested_repo(self, name):
        self.make_repo(self.work / name)
        r = self.ai("-w", answers=["y"])
        clone = self.project_dir() / "git"
        self.assertIn(("--bind", f"{clone}/{name}/.git", f"{MOUNT}/{name}/.git"), r.bwrap.mounts())
        self.assertIn(("--ro-bind", f"{self.work}/{name}/.git", f"/upstream/{name}/.git"),
                      r.bwrap.mounts())
        self.assertNotIn(f"{self.work}/{name}/.git", r.bwrap.sources("--bind"))

    @for_each_name()
    def test_read_only_by_star(self, name):
        (self.work / name).write_text("x")
        r = self.ai("-w", "--ro", "*")
        self.assertIn(("--ro-bind", f"{self.work}/{name}", f"{MOUNT}/{name}"), r.bwrap.mounts())

    @for_each_name()
    def test_read_only_in_a_directory(self, name):
        (self.work / name).mkdir()
        (self.work / name / "x.pdf").write_text("x")
        r = self.ai("-w", "--ro", "*.pdf")
        self.assertIn(("--ro-bind", f"{self.work}/{name}/x.pdf", f"{MOUNT}/{name}/x.pdf"),
                      r.bwrap.mounts())

    @for_each_name()
    def test_private_directories_are_hidden_from_the_checks(self, name):
        (self.work / name / "target").mkdir(parents=True)
        (self.outside / "f").write_text("x")
        os.link(self.outside / "f", self.work / name / "target/linked")
        r = self.ai("-w")
        self.assertOk(r)
        self.assertNotIn("Hard-linked", r.err)


@expand_names
class ProjectDirectory(AiTestCase):
    """The project's own path, chosen by you, but maybe by a tool."""

    def project(self, name):
        d = self.home / name
        d.mkdir()
        return d

    @for_each_name()
    def test_the_right_directory_is_mounted(self, name):
        d = self.project(name)
        # A decoy, for whoever mangles the name.
        decoy = self.home / name.rstrip("\n")
        decoy.mkdir(exist_ok=True)
        r = self.ai("-w", cwd=d)
        self.assertOk(r)
        self.assertIn(("--bind", str(d), f"/work/{name}"), r.bwrap.mounts())
        self.assertEqual((self.project_dir(d) / "path").read_bytes(), os.fsencode(f"{d}\n"))

    @for_each_name()
    def test_list(self, name):
        d = self.project(name)
        self.assertOk(self.ai("-w", cwd=d))
        r = self.ai("--list")
        self.assertShown(r.out, f"~/{name}")
        self.assertSafe(r.out)

    @for_each_name()
    def test_status(self, name):
        d = self.project(name)
        self.make_repo(d)
        self.assertOk(self.ai("-w", cwd=d, answers=["y"]))
        r = self.ai("--status", cwd=d)
        self.assertOk(r)
        self.assertShown(r.out, f"Project: ~/{name}")
        self.assertIn("nothing left to merge", r.out)
        self.assertSafe(r.out)

    @for_each_name()
    def test_same_project_again(self, name):
        d = self.project(name)
        self.assertOk(self.ai("-w", cwd=d))
        self.assertOk(self.ai("-w", cwd=d))

    @for_each_name()
    def test_forget(self, name):
        d = self.project(name)
        self.make_repo(d)
        self.assertOk(self.ai("-w", cwd=d, answers=["y"]))
        project = self.project_dir(d)
        self.assertTrue(project.exists())
        self.assertOk(self.ai("--forget", cwd=d, answers=["y"]))
        self.assertFalse(project.exists())
        self.assertNotIn("ai", self.git("remote", cwd=d).split())

    @for_each_name()
    def test_read_only_patterns(self, name):
        d = self.project(name)
        (d / "docs").mkdir()
        (d / "docs/x.pdf").write_text("x")
        r = self.ai("-w", "--ro", "docs/*.pdf", cwd=d)
        self.assertIn(("--ro-bind", f"{d}/docs/x.pdf", f"/work/{name}/docs/x.pdf"), r.bwrap.mounts())

    @for_each_name()
    def test_private_directories_are_hidden_from_the_checks(self, name):
        d = self.project(name)
        (d / "target").mkdir()
        (self.outside / "f").write_text("x")
        os.link(self.outside / "f", d / "target/linked")
        r = self.ai("-w", cwd=d)
        self.assertOk(r)
        self.assertNotIn("Hard-linked", r.err)

    @for_each_name()
    def test_notification_title(self, name):
        d = self.project(name)
        r = self.ai("-w", cwd=d, session=f'echo stop >"$NOTIFY"; {SETTLE}')
        self.assertEqual(len(r.notifications), 1)
        title = r.notifications[0][-2]
        # notify-send refuses what isn't UTF-8: those bytes are left out.
        self.assertShown(title, os.fsencode(name).decode("utf-8", "ignore"))
        self.assertSafe(title)


@expand_names
class Status(AiTestCase):
    @for_each_name()
    def test_nested_repo(self, name):
        self.make_repo(self.work / name)
        self.assertOk(self.ai("-w", answers=["y"]))
        r = self.ai("--status")
        self.assertOk(r)
        self.assertShown(r.out, f"Repo {name}: nothing left to merge")
        self.assertSafe(r.out)

    @for_each_name()
    def test_private_copy(self, name):
        (self.work / name / "target").mkdir(parents=True)
        self.assertOk(self.ai("-w"))
        r = self.ai("--status")
        self.assertShown(r.out, f"  {name}/target: ")
        self.assertSafe(r.out)

    @for_each_name()
    def test_home_layer(self, name):
        self.assertOk(self.ai("-w", session=f'touch "$LAYER"/{q(name)}'))
        r = self.ai("--status")
        self.assertShown(r.out, name)
        self.assertSafe(r.out)


@expand_names
class Promote(AiTestCase):
    def layer(self):
        return self.project_dir() / "home"

    def setUp(self):
        super().setUp()
        self.assertOk(self.ai("-w"))
        self.layer().mkdir(exist_ok=True)

    @for_each_name()
    def test_a_file(self, name):
        (self.layer() / name).write_text("project\n")
        r = self.ai("--promote", name, answers=["y"])
        self.assertOk(r)
        self.assertEqual((self.base / name).read_text(), "project\n")
        self.assertFalse((self.layer() / name).exists())
        self.assertSafe(r.out)

    @for_each_name()
    def test_a_file_named_in_a_directory(self, name):
        (self.layer() / "d").mkdir()
        (self.layer() / "d" / name).write_text("project\n")
        r = self.ai("--promote", "d", answers=["y"])
        self.assertOk(r)
        self.assertEqual((self.base / "d" / name).read_text(), "project\n")
        self.assertSafe(r.out)

    @for_each_name()
    def test_a_deletion(self, name):
        (self.layer() / "d").mkdir()
        os.mknod(self.layer() / "d" / name, 0o600 | 0o020000, 0)
        r = self.ai("--promote", "d", answers=["n"])
        self.assertShown(r.out, f"Delete ~/d/{name}")
        self.assertSafe(r.out)

    @for_each_name()
    def test_the_diff_labels(self, name):
        (self.layer() / "d").mkdir()
        (self.layer() / "d" / name).write_text("project\n")
        r = self.ai("--promote", "d", answers=["n"])
        self.assertSafe(r.out)
        # What you approve starts with its own two header lines.
        self.assertEqual(len([l for l in r.out.splitlines() if l.startswith(("---", "+++"))]), 2, r.out)

    @for_each_name()
    def test_a_refusal_leaves_it(self, name):
        (self.layer() / "d").mkdir()
        (self.layer() / "d" / name).write_text("project\n")
        r = self.ai("--promote", "d", answers=["n"])
        self.assertNotEqual(r.rc, 0)
        self.assertTrue((self.layer() / "d" / name).exists())
        self.assertSafe(r.out)

    @for_each_name()
    def test_a_symlink_target(self, name):
        (self.layer() / "link").symlink_to(f"/x/{name}")
        r = self.ai("--promote", "link", answers=["n"])
        self.assertShown(r.out, f"Symlink ~/link → /x/{name}")
        self.assertSafe(r.out)


@expand_names
class LiveNotifications(AiTestCase):
    @for_each_name()
    def test_tool_file_in_a_directory(self, name):
        (self.work / name).mkdir()
        r = self.ai("-w", session=f'touch "$WORK"/{q(name)}/.envrc; {SETTLE}')
        self.assertEqual(len(r.notifications), 1, r.notifications)
        body = r.notifications[0][-1]
        self.assertIn(".envrc was just created", body)
        self.assertSafe(body)

    @for_each_name()
    def test_tool_file_in_a_new_directory(self, name):
        r = self.ai("-w", session=f'mkdir "$WORK"/{q(name)}; touch "$WORK"/{q(name)}/.envrc; {SETTLE}')
        self.assertEqual(len(r.notifications), 1, r.notifications)
        self.assertSafe(r.notifications[0][-1])

    @for_each_name()
    def test_the_log_stays_one_line_per_entry(self, name):
        (self.work / name).mkdir()
        self.ai("-w", session=f'touch "$WORK"/{q(name)}/.envrc; {SETTLE}')
        lines = self.warnings.read_bytes().decode("utf-8", "surrogateescape").split("\n")
        live = [line for line in lines if "New during the session" in line]
        self.assertEqual(len(live), 1)
        self.assertSafe(live[0])


class Evasion(AiTestCase):
    """A name chosen to defeat the reports, not just to look odd."""

    @unittest.skipIf(IS_BASH, "bash compares what is shown of the links")
    def test_a_new_link_cannot_hide_behind_one_that_looks_the_same(self):
        os.symlink("/etc/passwd", self.work / "a\nb")
        r = self.ai("-w", session=f'ln -s /etc/passwd "$WORK"/{q("a" + chr(9) + "b")}')
        self.assertIn("New symlinks out of the work dir", r.err)

    def test_a_name_sorted_first_cannot_hide_the_tool_files(self):
        # "!" sorts before about any name, and an invalid byte makes the path
        # binary for grep, were it used on them.
        bad = q(b"!\xff")
        r = self.ai("-w", session=f'mkdir "$WORK"/{bad}; touch "$WORK"/{bad}/.npmrc; '
                                  'touch "$WORK/.envrc"; git init -q "$WORK/new"')
        log = self.warnings.read_bytes().decode("utf-8", "replace") if self.warnings.exists() else ""
        for text in (r.err, log):
            self.assertIn(f"{self.work}/.envrc", text)
            self.assertIn(f"{self.work}/new/.git", text)

    def test_a_name_cannot_leave_a_tool_file_unreported(self):
        # A newline and invalid bytes: each report must mention it.
        bad = q(b"\xff\n\xff")
        r = self.ai("-w", session=f'mkdir "$WORK"/{bad}; touch "$WORK"/{bad}/.envrc; {SETTLE}')
        self.assertTrue((self.work / os.fsdecode(b"\xff\n\xff") / ".envrc").exists())
        log = self.warnings.read_bytes().decode("utf-8", "replace") if self.warnings.exists() else ""
        self.assertIn(".envrc", r.err)
        self.assertIn(".envrc", log)
        self.assertEqual(len(r.notifications), 1, r.notifications)
        self.assertNotIn("binary file matches", r.err)

    def test_a_name_cannot_leave_a_new_repo_unreported(self):
        bad = q(b"\xff\n\xff")
        r = self.ai("-w", session=f'mkdir "$WORK"/{bad}; git init -q "$WORK"/{bad}; {SETTLE}')
        self.assertTrue((self.work / os.fsdecode(b"\xff\n\xff") / ".git").is_dir())
        log = self.warnings.read_bytes().decode("utf-8", "replace") if self.warnings.exists() else ""
        self.assertIn(".git", r.err)
        self.assertIn(".git", log)
        self.assertEqual(len(r.notifications), 1, r.notifications)
        self.assertNotIn("binary file matches", r.err)

    def test_a_tab_ending_a_name_cannot_hide_a_new_directory(self):
        # What the new directory holds when inotifywait gets to watch it is
        # only found through its exact path.
        bad = q("d\t")
        r = self.ai("-w", session=f'mkdir "$WORK"/.tmp; touch "$WORK"/.tmp/.envrc; '
                                  f'mv "$WORK"/.tmp "$WORK"/{bad}; {SETTLE}')
        self.assertEqual(len(r.notifications), 2, r.notifications)
        self.assertIn("d?/.envrc", r.notifications[-1][-1])

    def test_a_file_name_cannot_forge_a_notification(self):
        # One file, whose name looks like a second line of events.
        r = self.ai("-w", session=f'touch "$WORK"/{q("x" + chr(10) + "CREATE" + chr(9) + ".envrc")}; {SETTLE}')
        self.assertEqual(r.notifications, [])
        self.assertFalse((self.work / ".envrc").exists())


if __name__ == "__main__":
    unittest.main()
