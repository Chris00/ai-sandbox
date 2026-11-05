"""What a session reports: at exit, in the log and on the desktop."""
import os
import time
import unittest

from harness import STUBS, AiTestCase, q

SETTLE = "sleep 0.3"      # for the watcher of the work dir to see events


class ExitReport(AiTestCase):
    def test_a_quiet_session_reports_nothing(self):
        r = self.ai("-w", session='echo hi >"$WORK/plain.txt"')
        self.assertOk(r)
        self.assertNotIn("Session in", r.err)
        self.assertFalse(self.warnings.exists())

    def test_new_symlink_out_of_the_work_dir(self):
        r = self.ai("-w", session='ln -s /etc/passwd "$WORK/link"; ln -s plain "$WORK/inside"')
        self.assertIn("New symlinks out of the work dir", r.err)
        self.assertIn("./link → /etc/passwd", r.err)
        self.assertNotIn("./inside", r.err)

    def test_a_symlink_there_before_is_not_new(self):
        (self.work / "old").symlink_to("/etc/passwd")
        r = self.ai("-w")
        self.assertNotIn("New symlinks", r.err)

    def test_a_symlink_changed_to_lead_out(self):
        (self.work / "x").write_text("")
        (self.work / "old").symlink_to("x")
        r = self.ai("-w", session='ln -sf /etc/passwd "$WORK/old"')
        self.assertIn("./old → /etc/passwd", r.err)

    def test_new_tool_file(self):
        (self.work / "sub").mkdir()
        r = self.ai("-w", session='touch "$WORK/sub/.envrc"')
        self.assertIn("can run code on the host", r.err)
        self.assertIn(f"{self.work}/sub/.envrc", r.err)

    def test_changed_tool_file_in_a_directory_read_only_later(self):
        # .vscode is protected at the start, so changes to it are yours.
        (self.work / ".vscode").mkdir()
        r = self.ai("-w", session=f'touch "$WORK/.vscode/tasks.json"; {SETTLE}')
        self.assertNotIn("can run code on the host", r.err)

    def test_a_tool_file_in_a_new_directory(self):
        r = self.ai("-w", session='mkdir -p "$WORK/a/b"; touch "$WORK/a/b/.npmrc"')
        self.assertIn(f"{self.work}/a/b/.npmrc", r.err)

    def test_new_git_repo(self):
        r = self.ai("-w", session='git init -q "$WORK/new"')
        self.assertIn(f"{self.work}/new/.git", r.err)

    def test_changed_config_of_your_repo_seen_from_outside(self):
        self.make_repo()
        r = self.ai("-w", answers=["y"], session='echo >>"$WORK/.git/config"')
        self.assertIn(f"{self.work}/.git", r.err)

    def test_new_hook_of_your_repo_seen_from_outside(self):
        self.make_repo()
        r = self.ai("-w", answers=["y"], session='touch "$WORK/.git/hooks/pre-commit"')
        self.assertIn(f"{self.work}/.git", r.err)

    def test_objects_are_not_a_change_to_report(self):
        self.make_repo()
        r = self.ai("-w", answers=["y"], session='touch "$WORK/.git/objects/x"')
        self.assertNotIn("can run code on the host", r.err)

    def test_setting_up_your_repo_is_not_a_change_to_report(self):
        self.make_repo()
        r = self.ai("-w", answers=["y"])
        self.assertNotIn("can run code on the host", r.err)

    def test_the_report_survives_a_failing_session(self):
        r = self.ai("-w", session='ln -s /etc/passwd "$WORK/link"; exit 3')
        self.assertEqual(r.rc, 3)
        self.assertIn("./link → /etc/passwd", r.err)

    def test_the_report_goes_to_the_log_with_its_date(self):
        self.ai("-w", session='ln -s /etc/passwd "$WORK/link"')
        log = self.warnings.read_text()
        self.assertRegex(log, r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d Session in ")
        self.assertIn("./link → /etc/passwd", log)

    def test_the_mount_points_left_for_private_directories_are_removed(self):
        (self.work / "package.json").write_text("{}")
        r = self.ai("-w")
        self.assertFalse((self.work / "node_modules").exists())
        self.assertNotIn("node_modules", r.err.split("Private in the sandbox")[-1]
                         .split("\n", 1)[-1])

    def test_a_mount_point_the_agent_filled_is_kept(self):
        # Nothing of what is left there may be deleted.
        (self.work / "package.json").write_text("{}")
        self.ai("-w", session='touch "$WORK/node_modules/kept"')
        self.assertTrue((self.work / "node_modules/kept").exists())


class HomeReport(AiTestCase):
    def changed(self, *paths, base=False):
        """The report once the session changed `paths` of the agent's home."""
        root = '"$BASE"' if base else '"$LAYER"'
        script = "".join(
            f'mkdir -p {root}/{q(os.path.dirname(p))}; echo x >{root}/{q(p)}\n' if os.path.dirname(p)
            else f'echo x >{root}/{q(p)}\n' for p in paths)
        return self.ai(*([] if base else ["-w"]), session=script)

    def test_nothing_changed(self):
        self.assertNotIn("Changed in the agent's", self.ai("-w").err)

    def test_changes_to_the_layer_are_all_shown(self):
        r = self.changed("notes.txt", "dir/x.txt")
        self.assertIn("Changed in the agent's home, in this project only", r.err)
        self.assertIn("  notes.txt\n", r.err)
        self.assertIn("  dir/x.txt\n", r.err)

    def test_triggers_are_marked_and_explained(self):
        r = self.changed(".bashrc", ".local/bin/tool", ".claude/settings.json")
        for line in ("! .bashrc\n", "! .local/bin/tool\n", "! .claude/settings.json\n"):
            self.assertIn(line, r.err)
        self.assertIn("!: can run code, or steer the agent", r.err)
        self.assertIn("ai --promote PATH", r.err)

    def test_only_triggers_go_to_the_log(self):
        self.changed(".bashrc", "notes.txt")
        log = self.warnings.read_text()
        self.assertIn("! .bashrc", log)
        self.assertNotIn("notes.txt", log)

    def test_harmless_changes_stay_out_of_the_log(self):
        r = self.changed("notes.txt")
        self.assertIn("notes.txt", r.err)
        self.assertFalse(self.warnings.exists())

    def test_ignored_files_are_only_counted(self):
        r = self.changed(".bash_history", ".cache/a", ".cache/b", "notes.txt")
        self.assertIn("  notes.txt\n", r.err)
        self.assertIn("and 3 more in HOME_IGNORE", r.err)
        self.assertNotIn(".bash_history", r.err)

    def test_only_ignored_files_is_nothing_to_report(self):
        r = self.changed(".bash_history")
        self.assertNotIn("Changed in the agent's", r.err)

    def test_a_trigger_inside_an_ignored_directory_is_shown(self):
        self.write_config(add=dict(HOME_TRIGGERS=[".cache/evil"]))
        r = self.changed(".cache/evil")
        self.assertIn("! .cache/evil", r.err)

    def test_groups_come_first_as_one_line(self):
        r = self.changed("zzz.txt", ".claude/skills/foo/SKILL.md", ".claude/skills/foo/run.py",
                         ".claude/plugins/cache/mkt/plug/a", ".claude/plugins/cache/mkt/plug/b")
        err = r.err
        self.assertIn("! .claude/skills/foo/ (2 files)\n", err)
        self.assertIn("! .claude/plugins/cache/mkt/plug/ (2 files)\n", err)
        self.assertLess(err.index(".claude/skills/foo/"), err.index(".claude/plugins"))
        self.assertLess(err.index(".claude/plugins"), err.index("zzz.txt"))

    def test_a_group_with_one_file_shows_it(self):
        r = self.changed(".claude/skills/foo/SKILL.md")
        self.assertIn("! .claude/skills/foo/SKILL.md\n", r.err)

    def test_the_rest_is_grouped_two_levels_down(self):
        r = self.changed("a/b/c/1", "a/b/d/2", "a/e/3")
        self.assertIn("  a/b/ (2 files)\n", r.err)
        self.assertIn("  a/e/3\n", r.err)

    def test_a_deletion_is_shown(self):
        r = self.ai("-w", session='mknod "$LAYER/gone" c 0 0')
        self.assertIn("gone (deleted)", r.err)

    def test_base_home_changes_are_for_every_project(self):
        r = self.changed(".bashrc", base=True)
        self.assertIn("Changed in the agent's base home, seen by every project", r.err)
        self.assertIn("! .bashrc", r.err)
        self.assertNotIn("ai --promote", r.err)

    def test_base_home_only_watches_triggers(self):
        r = self.changed("notes.txt", base=True)
        self.assertNotIn("notes.txt", r.err)

    def test_changes_before_the_session_are_not_reported(self):
        layer = self.project_dir() / "home"
        layer.mkdir(parents=True)
        (self.project_dir() / "path").write_text(f"{self.work}\n")
        (layer / ".bashrc").write_text("x")
        r = self.ai("-w")
        self.assertNotIn(".bashrc", r.err)

    def test_a_rewritten_trigger_is_reported(self):
        self.changed(".bashrc")
        r = self.ai("-w", session='sleep 0.02; echo y >"$LAYER/.bashrc"')
        self.assertIn("! .bashrc", r.err)

    def test_resetting_the_time_does_not_hide_a_change(self):
        self.changed(".bashrc")
        r = self.ai("-w", session='t=$(stat -c %y "$LAYER/.bashrc"); sleep 0.02;'
                                  'echo y >"$LAYER/.bashrc"; touch -d "$t" "$LAYER/.bashrc"')
        self.assertIn("! .bashrc", r.err)

    def test_an_unreadable_directory_does_not_break_the_report(self):
        r = self.ai("-w", session='mkdir "$LAYER/locked"; touch "$LAYER/.bashrc"; chmod 000 "$LAYER/locked"')
        self.assertOk(r)
        self.assertIn("! .bashrc", r.err)


class LiveNotifications(AiTestCase):
    def test_changes_to_a_protected_entry_are_yours(self):
        # Out of the agent's reach: only you can have made them.
        (self.work / ".envrc").touch()
        r = self.ai("-w", session=f'rm "$WORK/.envrc"; touch "$WORK/.envrc"; {SETTLE}')
        self.assertOk(r)
        self.assertEqual(r.notifications, [])

    def test_a_new_tool_file_is_reported_during_the_session(self):
        (self.work / "sub").mkdir()
        r = self.ai("-w", session=f'touch "$WORK/sub/.envrc"; {SETTLE}')
        self.assertEqual(len(r.notifications), 1, r.notifications)
        self.assertIn(".envrc", r.notifications[0][-2])
        self.assertIn("proj/sub/.envrc", r.notifications[0][-1])
        self.assertIn("New during the session", self.warnings.read_text())

    def test_it_is_critical(self):
        r = self.ai("-w", session=f'touch "$WORK/.envrc"; {SETTLE}')
        self.assertIn("critical", r.notifications[0])

    def test_a_tool_file_in_a_new_directory(self):
        r = self.ai("-w", session=f'mkdir -p "$WORK/a/b"; touch "$WORK/a/b/.envrc"; {SETTLE}')
        self.assertTrue(any("a/b/.envrc" in n[-1] for n in r.notifications), r.notifications)

    def test_a_tool_file_moved_in(self):
        (self.outside / ".envrc").write_text("x")
        r = self.ai("-w", session=f'mv {q(str(self.outside / ".envrc"))} "$WORK/.envrc"; {SETTLE}')
        self.assertTrue(any(".envrc" in n[-1] for n in r.notifications), r.notifications)

    def test_a_directory_with_a_tool_file_moved_in(self):
        (self.outside / "d").mkdir()
        (self.outside / "d/.envrc").write_text("x")
        r = self.ai("-w", session=f'mv {q(str(self.outside / "d"))} "$WORK/d"; {SETTLE}')
        self.assertTrue(any("d/.envrc" in n[-1] for n in r.notifications), r.notifications)

    def test_each_is_reported_once(self):
        r = self.ai("-w", session=f'touch "$WORK/.envrc"; rm "$WORK/.envrc"; touch "$WORK/.envrc"; {SETTLE}')
        self.assertEqual(len(r.notifications), 1, r.notifications)

    def test_protected_entries_are_not_reported(self):
        (self.work / "package.json").write_text("{}")
        r = self.ai("-w", session=SETTLE)
        self.assertEqual(r.notifications, [])

    def test_contents_of_private_directories_do_not_matter(self):
        # Your own builds there would only cost watches.
        (self.work / "target/a").mkdir(parents=True)
        r = self.ai("-w", session=f'touch "$WORK/target/a/.envrc"; {SETTLE}')
        self.assertEqual(r.notifications, [])

    def test_ordinary_files_are_not_reported(self):
        r = self.ai("-w", session=f'touch "$WORK/ordinary"; mkdir "$WORK/dir"; {SETTLE}')
        self.assertEqual(r.notifications, [])

    def test_the_body_is_not_read_as_markup(self):
        d = self.work / "<b>x&y"
        d.mkdir()
        r = self.ai("-w", session=f'touch {q(str(d))}/.envrc; {SETTLE}')
        self.assertIn("&lt;b&gt;x&amp;y", r.notifications[0][-1])
        self.assertNotIn("<b>", r.notifications[0][-1])


class AgentEvents(AiTestCase):
    def events(self, script):
        r = self.ai("-w", session=f'{script}; {SETTLE}')
        self.assertOk(r)
        return r.notifications

    def test_known_events(self):
        for word, text in (("stop", "has finished"), ("permission_prompt", "asks for your permission"),
                           ("idle_prompt", "is waiting for you"),
                           ("elicitation_dialog", "has a question"), ("input", "needs your attention")):
            with self.subTest(word=word):
                found = self.events(f'echo {word} >"$NOTIFY"')
                self.assertEqual(len(found), 1, found)
                self.assertIn(text, found[0][-1])
                self.assertIn("proj", found[0][-2])

    def test_they_do_not_stay_on_the_desktop(self):
        found = self.events('echo stop >"$NOTIFY"')
        self.assertIn("--hint=boolean:transient:true", found[0])

    def test_the_sandbox_never_chooses_the_text(self):
        for word in ("<b>evil</b>", "rm -rf", "evil", "STOP", "stop now", "$(touch x)", "__proto__", "x" * 64):
            with self.subTest(word=word):
                self.assertEqual(self.events(f'echo {q(word)} >"$NOTIFY"'), [])

    def test_a_flood_is_limited(self):
        found = self.events('for i in 1 2 3 4 5 6 7 8 9 10; do echo stop >"$NOTIFY"; done')
        self.assertEqual(len(found), 1, found)

    def test_the_limit_is_per_event(self):
        found = self.events('echo stop >"$NOTIFY"; echo input >"$NOTIFY"')
        self.assertEqual(len(found), 2, found)

    def test_a_writer_never_blocks_and_closing_changes_nothing(self):
        found = self.events('echo stop >"$NOTIFY"; sleep 0.1; exec 3>"$NOTIFY"; exec 3>&-; echo input >"$NOTIFY"')
        self.assertEqual(len(found), 2, found)

    def test_the_last_event_is_shown(self):
        # Written as the session ends, with garbage before it to read
        # first: it isn't lost at exit.
        r = self.ai("-w", session='{ head -c 10000 /dev/urandom | tr -d "\\n"; echo; '
                                  'echo stop; } >"$NOTIFY"')
        self.assertOk(r)
        self.assertEqual(len(r.notifications), 1, r.notifications)
        self.assertEqual(r.leftovers, [])

    def test_binary_garbage_is_ignored(self):
        found = self.events('{ head -c 10000 /dev/urandom | tr -d "\\n"; echo; } >"$NOTIFY"; sleep 0.5; echo stop >"$NOTIFY"')
        self.assertEqual(len(found), 1, found)

    def test_the_base_home_session_says_so(self):
        r = self.ai(session=f'echo stop >"$NOTIFY"; {SETTLE}')
        self.assertIn("the agent's home", r.notifications[0][-2])

    def test_notify_send_may_be_missing(self):
        # A PATH without notify-send, but with what ai needs.
        hide = self.outside / "bin"
        hide.mkdir()
        for d in [STUBS, *os.environ["PATH"].split(":"), "/usr/sbin", "/sbin"]:
            if os.path.isdir(d):
                for tool in os.listdir(d):
                    if tool != "notify-send" and not os.path.lexists(hide / tool):
                        (hide / tool).symlink_to(os.path.join(d, tool))
        r = self.ai("-w", env={"PATH": str(hide)},
                    session=f'echo stop >"$NOTIFY"; touch "$WORK/.envrc"; {SETTLE}')
        self.assertOk(r)
        self.assertIn("notify-send is missing", r.err)
        self.assertIn("New during the session", self.warnings.read_text())


if __name__ == "__main__":
    unittest.main()
