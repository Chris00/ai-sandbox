"""What the sandbox is made of: the arguments given to bwrap."""
import os
import unittest

from harness import AiTestCase


class Isolation(AiTestCase):
    def test_the_sandbox_is_shut_off(self):
        for args in ([], ["-w"]):
            with self.subTest(args=args):
                flags = self.ai(*args).bwrap.flags()
                for flag in ("--unshare-all", "--clearenv", "--die-with-parent",
                             "--new-session", "--disable-userns", "--unshare-user"):
                    self.assertIn(flag, flags)

    def test_the_host_network_is_not_shared(self):
        self.assertNotIn("--share-net", self.ai("-w").bwrap.argv)

    def test_only_expected_paths_are_visible(self):
        r = self.ai("-w")
        allowed = ("/usr", "/lib", "/lib64", "/bin", "/var/lib/texmf", "/etc/",
                   str(self.work), str(self.tmp), f"{self.home}/texmf")
        for source in r.bwrap.sources():
            self.assertTrue(source.startswith(allowed), source)

    def test_your_etc_files_are_mounted_read_only(self):
        etc = self.home / "AI/etc"
        (etc / "claude-code").mkdir(parents=True)
        (etc / "gitconfig").write_text("")
        for args in ([], ["-w"]):
            with self.subTest(args=args):
                r = self.ai(*args)
                mounts = [m for m in r.bwrap.mounts() if m[1].startswith(f"{etc}/")]
                self.assertEqual(mounts, [("--ro-bind", f"{etc}/claude-code", "/etc/claude-code"),
                                          ("--ro-bind", f"{etc}/gitconfig", "/etc/gitconfig")])

    def test_no_etc_files_without_the_directory(self):
        r = self.ai("-w")
        self.assertFalse([s for s in r.bwrap.sources() if s.startswith(f"{self.home}/AI/etc")])

    def test_the_real_home_is_never_mounted(self):
        r = self.ai("-w")
        for source in r.bwrap.sources():
            self.assertNotEqual(source.rstrip("/"), str(self.home))

    def test_clearenv_comes_before_any_variable(self):
        flags = self.ai("-w").bwrap.flags()
        self.assertLess(flags.index("--clearenv"), flags.index("--setenv"))

    def test_the_environment_is_not_passed_on(self):
        r = self.ai("-w", env={"SECRET_TOKEN": "hunter2", "SSH_AUTH_SOCK": "/x"})
        self.assertNotIn("hunter2", repr(r.bwrap.argv))
        self.assertNotIn("SECRET_TOKEN", r.bwrap.env())
        self.assertNotIn("SSH_AUTH_SOCK", r.bwrap.env())

    def test_a_seccomp_filter_is_given(self):
        r = self.ai("-w")
        # Whole BPF instructions, of 8 bytes.
        self.assertGreater(r.bwrap.seccomp_size, 0)
        self.assertEqual(r.bwrap.seccomp_size % 8, 0)

    def test_the_seccomp_filter_is_deterministic(self):
        # So that a rewrite can be compared with the original, byte by byte.
        first = (self.ai("-w").log / "seccomp.bpf").read_bytes()
        second = (self.ai("-w").log / "seccomp.bpf").read_bytes()
        self.assertEqual(first, second)


class Network(AiTestCase):
    def test_firewall_rules(self):
        rules = self.ai("-w").logged("nft.rules")
        for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                      "100.64.0.0/10", "fc00::/7", "fe80::/10"):
            self.assertIn(block, rules)
        self.assertIn("10.0.2.3", rules)    # the DNS forwarder
        self.assertIn("reject", rules)

    def test_slirp4netns_cannot_reach_the_host(self):
        args = self.ai("-w").logged("slirp.args")
        for option in ("--disable-host-loopback", "--enable-sandbox", "--enable-seccomp"):
            self.assertIn(option, args)

    def test_the_sandbox_waits_for_the_network(self):
        at_release = self.ai("-w").bwrap.at_release
        self.assertIn("nft.rules", at_release)
        self.assertIn("slirp.args", at_release)

    def test_no_session_without_the_firewall(self):
        r = self.ai("-w", session='touch "$WORK/ran"', env={"AI_TEST_NFT_FAIL": "1"})
        self.assertNotEqual(r.rc, 0, repr(r))
        self.assertIn("could not install the firewall", r.err)
        self.assertFalse((self.work / "ran").exists())

    def test_no_session_without_slirp4netns(self):
        r = self.ai("-w", session='touch "$WORK/ran"', env={"AI_TEST_SLIRP_FAIL": "1"})
        self.assertNotEqual(r.rc, 0, repr(r))
        self.assertIn("slirp4netns stopped", r.err)
        self.assertFalse((self.work / "ran").exists())

    def test_no_process_is_left_behind(self):
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/id_ed25519_ai").write_text("key")
        r = self.ai("-w", "--ssh", session="sleep 0.1")
        self.assertOk(r)
        self.assertEqual(r.leftovers, [])

    def test_no_process_is_left_behind_after_a_failure(self):
        r = self.ai("-w", session="exit 3", env={"AI_TEST_NFT_FAIL": "1"})
        self.assertEqual(r.leftovers, [])


class Environment(AiTestCase):
    def test_the_variables_ai_sets(self):
        env = self.ai("-w").bwrap.env()
        self.assertEqual(env["HOME"], str(self.home))
        self.assertEqual(env["USER"], "tester")
        self.assertEqual(env["AI_NOTIFY"], "/run/ai/notify")
        self.assertIn("/usr/bin", env["PATH"])

    def test_ai_project_is_only_set_in_projects(self):
        self.assertIn("AI_PROJECT", self.ai("-w").bwrap.env())
        self.assertNotIn("AI_PROJECT", self.ai().bwrap.env())

    def test_secrets_stay_off_the_command_line(self):
        self.write_config("AI_ENV=(TOKEN=hunter2)")
        r = self.ai("-w")
        self.assertEqual(r.bwrap.env()["TOKEN"], "hunter2")
        self.assertNotIn("hunter2", repr(r.bwrap.argv))
        self.assertIn(["--setenv", "TOKEN", "hunter2"], r.bwrap.piped)

    def test_values_are_kept_exactly(self):
        value = "a b\tc\nd=e*'\"$(f)"
        escaped = "".join(f"\\x{b:02x}" for b in value.encode())
        self.write_config(f"AI_ENV=($'V={escaped}')")
        self.assertEqual(self.ai("-w").bwrap.env()["V"], value)

    def test_project_variables_only_in_projects(self):
        self.write_config("AI_PROJECT_ENV=(ONLY=project)", "AI_ENV=(ALWAYS=yes)")
        env = self.ai("-w").bwrap.env()
        self.assertEqual((env["ONLY"], env["ALWAYS"]), ("project", "yes"))
        env = self.ai().bwrap.env()
        self.assertNotIn("ONLY", env)
        self.assertEqual(env["ALWAYS"], "yes")

    def test_auto_updates_are_off_in_projects_only(self):
        self.assertEqual(self.ai("-w").bwrap.env()["DISABLE_AUTOUPDATER"], "1")
        self.assertNotIn("DISABLE_AUTOUPDATER", self.ai().bwrap.env())

    def test_configured_variables_can_replace_those_of_ai(self):
        self.write_config("AI_ENV=(PATH=/custom)")
        self.assertEqual(self.ai("-w").bwrap.env()["PATH"], "/custom")


class WorkDir(AiTestCase):
    def test_without_work_dir_the_base_home_is_read_write(self):
        r = self.ai()
        self.assertIn(("--bind", f"{self.base}/", f"{self.home}/"), r.bwrap.mounts())
        self.assertEqual(r.bwrap.chdir(), str(self.home))
        self.assertNotIn("--overlay", r.bwrap.flags())
        self.assertNotIn(str(self.work), r.bwrap.sources())

    def test_in_a_project_the_home_is_an_overlay(self):
        r = self.ai("-w")
        project = self.project_dir()
        self.assertIn(["--overlay-src", str(self.base)], r.bwrap.ops)
        self.assertIn(["--overlay", f"{project}/home", f"{project}/home.work", str(self.home)],
                      r.bwrap.ops)
        self.assertFalse([m for m in r.bwrap.mounts() if m[1].startswith(str(self.base))])

    def test_mount_path_under_the_home(self):
        r = self.ai("-w")
        self.assertIn(("--bind", str(self.work), "/work/software/proj"), r.bwrap.mounts())
        self.assertEqual(r.bwrap.chdir(), "/work/software/proj")
        self.assertEqual(r.bwrap.env()["AI_PROJECT"], "/work/software/proj")

    def test_mount_path_outside_the_home(self):
        r = self.ai("-w", cwd=self.outside)
        target = f"/work-tmp{self.outside}"
        self.assertIn(("--bind", str(self.outside), target), r.bwrap.mounts())

    def test_a_symlinked_work_dir_is_resolved(self):
        link = self.home / "link"
        link.symlink_to(self.work)
        r = self.ai("-w", cwd=link)
        self.assertIn(("--bind", str(self.work), "/work/software/proj"), r.bwrap.mounts())

    def test_the_project_records_its_path(self):
        self.assertOk(self.ai("-w"))
        self.assertEqual((self.project_dir() / "path").read_text(), f"{self.work}\n")

    def test_temporary_files_are_removed(self):
        self.assertOk(self.ai("-w"))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_temporary_files_are_removed_on_failure(self):
        self.ai("-w", env={"AI_TEST_NFT_FAIL": "1"})
        self.assertEqual(os.listdir(self.tmp), [])

    def test_ssh_agent(self):
        key = self.home / ".ssh/id_ed25519_ai"
        key.parent.mkdir()
        key.write_text("key")
        (self.home / ".ssh/known_hosts").write_text("x")
        r = self.ai("-w", "--ssh", session="sleep 0.1")
        self.assertOk(r)
        env = r.bwrap.env()
        self.assertEqual(env["SSH_AUTH_SOCK"], "/run/ssh-agent.sock")
        self.assertIn("/run/ssh-agent.sock", r.bwrap.targets("--bind"))
        self.assertIn(("--ro-bind-try", str(self.home / ".ssh/known_hosts"),
                       "/etc/ssh/ssh_known_hosts"), r.bwrap.mounts())
        # Never the key itself.
        self.assertNotIn(str(key), r.bwrap.sources())
        added = r.logged("ssh-add.args").splitlines()
        self.assertIn(str(key), added)
        for host in ("github.com", "gitlab.com"):
            self.assertIn(host, added)
        self.assertEqual(r.leftovers, [], "the agent is still running")

    def test_no_ssh_agent_by_default(self):
        r = self.ai("-w", env={"SSH_AUTH_SOCK": "/real/agent"})
        self.assertNotIn("SSH_AUTH_SOCK", r.bwrap.env())
        self.assertNotIn("/run/ssh-agent.sock", r.bwrap.targets())


class Repos(AiTestCase):
    def setUp(self):
        super().setUp()
        self.make_repo()
        self.clone = self.project_dir() / "git"

    def test_a_new_repo_needs_your_word(self):
        r = self.ai("-w", answers=["n"])
        self.assertRefused(r, "Not trusting")
        self.assertFalse(self.clone.exists())

    def test_the_real_git_dir_is_hidden_behind_a_clone(self):
        r = self.ai("-w", answers=["y"])
        self.assertIn(("--bind", f"{self.clone}/.git", "/work/software/proj/.git"),
                      r.bwrap.mounts())
        self.assertIn(("--ro-bind", f"{self.work}/.git", "/upstream/.git"), r.bwrap.mounts())
        writable = r.bwrap.sources("--bind")
        self.assertNotIn(f"{self.work}/.git", writable)

    def test_the_clone_hides_after_the_work_dir_is_mounted(self):
        r = self.ai("-w", answers=["y"])
        targets = r.bwrap.targets()
        self.assertLess(targets.index("/work/software/proj"),
                        targets.index("/work/software/proj/.git"))

    def test_the_clone_has_your_commits_and_its_origin_is_the_view(self):
        self.ai("-w", answers=["y"])
        self.assertIn("first", self.git("log", "--oneline", cwd=self.clone))
        self.assertEqual(self.git("remote", "get-url", "origin", cwd=self.clone).strip(), "/upstream")

    def test_your_repo_gets_a_fetch_only_remote(self):
        self.ai("-w", answers=["y"])
        self.assertEqual(self.git("config", "remote.ai.url").strip(), str(self.clone))
        self.assertEqual(self.git("config", "remote.ai.tagOpt").strip(), "--no-tags")
        self.assertEqual(self.git("config", "fetch.fsckObjects").strip(), "true")
        self.assertEqual(self.git("config", "remote.ai.fetch").strip(),
                         "+refs/heads/*:refs/remotes/ai/*")

    def test_the_second_session_does_not_ask(self):
        self.assertOk(self.ai("-w", answers=["y"]))
        r = self.ai("-w")
        self.assertOk(r)
        self.assertNotIn("did you create it", r.err)

    def test_a_repo_created_by_the_agent_is_not_trusted(self):
        self.assertOk(self.ai("-w", answers=["y"], session="git init -q sub"))
        self.assertRefused(self.ai("-w"), "Not trusting")

    def test_no_hook_of_your_repo_runs(self):
        marker = self.outside / "ran"
        for hook in ("post-checkout", "pre-commit", "post-index-change", "reference-transaction"):
            path = self.work / ".git/hooks" / hook
            path.write_text(f"#!/bin/sh\ntouch {marker}\n")
            path.chmod(0o755)
        self.assertOk(self.ai("-w", answers=["y"]))
        self.assertFalse(marker.exists())

    def test_nested_repos_shallowest_first(self):
        self.make_repo(self.work / "sub/deep")
        self.make_repo(self.work / "sub")
        r = self.ai("-w", answers=["y", "y", "y"])
        self.assertOk(r)
        targets = [t for t in r.bwrap.targets("--bind") if t.endswith("/.git")]
        self.assertEqual(targets, ["/work/software/proj/.git",
                                   "/work/software/proj/sub/.git",
                                   "/work/software/proj/sub/deep/.git"])
        self.assertIn(("--ro-bind", f"{self.work}/sub/deep/.git", "/upstream/sub/deep/.git"),
                      r.bwrap.mounts())

    def test_a_symlinked_config_is_refused(self):
        config = self.work / ".git/config"
        target = self.outside / "config"
        target.write_text(config.read_text())
        before = target.read_text()
        config.unlink()
        config.symlink_to(target)
        self.assertRefused(self.ai("-w", answers=["y"]), "is not a plain file")
        self.assertEqual(target.read_text(), before)

    def test_a_hard_linked_config_is_refused(self):
        config = self.work / ".git/config"
        target = self.outside / "config"
        os.link(config, target)
        before = target.read_text()
        self.assertRefused(self.ai("-w", answers=["y"]), "is not a plain file")
        self.assertEqual(target.read_text(), before)

    def test_hooks_kept_in_the_tree_are_pointed_out(self):
        self.git("config", "core.hooksPath", ".husky")
        r = self.ai("-w", answers=["y"])
        self.assertIn("core.hooksPath is inside the work tree", r.err)

    def test_a_git_link_into_the_work_dir_needs_your_word(self):
        data = self.work / "gitdata"
        self.make_repo(self.work / "other")
        (self.work / "other/.git").rename(data)
        (self.work / "other/.git").write_text(f"gitdir: {data}\n")
        r = self.ai("-w", answers=["y", "n"])
        self.assertNotEqual(r.rc, 0, repr(r))
        self.assertIn("NOT protected", r.err)
        self.assertIsNone(r.bwrap)

    def test_a_git_link_outside_the_work_dir_is_only_reported(self):
        data = self.outside / "gitdata"
        self.make_repo(self.work / "other")
        (self.work / "other/.git").rename(data)
        (self.work / "other/.git").write_text(f"gitdir: {data}\n")
        r = self.ai("-w", answers=["y"])
        self.assertOk(r)
        self.assertIn("Not handled (Git data outside the work dir)", r.err)
        self.assertNotIn(str(data), r.bwrap.sources())


class ToolFiles(AiTestCase):
    MOUNT = "/work/software/proj"

    def test_read_only_files(self):
        for name in (".envrc", ".dir-locals.el", ".npmrc", ".gitattributes"):
            (self.work / name).write_text("x")
        (self.work / "sub/.vscode").mkdir(parents=True)
        mounts = self.ai("-w").bwrap.mounts()
        for rel in (".envrc", ".dir-locals.el", ".npmrc", ".gitattributes", "sub/.vscode"):
            self.assertIn(("--ro-bind", f"{self.work}/{rel}", f"{self.MOUNT}/{rel}"), mounts)

    def test_private_directories_are_replaced(self):
        for rel in ("target", "sub/node_modules", ".venv"):
            (self.work / rel).mkdir(parents=True)
        r = self.ai("-w")
        shadow = self.project_dir() / "shadow"
        for rel in ("target", "sub/node_modules", ".venv"):
            self.assertIn(("--bind", f"{shadow}/{rel}", f"{self.MOUNT}/{rel}"), r.bwrap.mounts())
            self.assertTrue((shadow / rel).is_dir())

    def test_your_private_directories_are_left_alone(self):
        (self.work / "target").mkdir()
        (self.work / "target/app").write_text("yours")
        self.assertOk(self.ai("-w", session='touch "$WORK/untouched"'))
        self.assertEqual(os.listdir(self.work / "target"), ["app"])

    def test_private_directories_a_tool_would_create(self):
        for signal, name in (("Cargo.toml", "target"), ("package.json", "node_modules"),
                             ("pyproject.toml", ".venv")):
            (self.work / signal).write_text("x")
        r = self.ai("-w", session='test -d "$WORK/target"')
        self.assertOk(r)
        for name in ("target", "node_modules", ".venv"):
            self.assertIn(f"{self.MOUNT}/{name}", r.bwrap.targets("--bind"))
            # The mount point doesn't stay in your tree.
            self.assertFalse((self.work / name).exists(), name)

    def test_no_private_directory_without_its_signal(self):
        r = self.ai("-w")
        self.assertNotIn(f"{self.MOUNT}/target", r.bwrap.targets())
        self.assertFalse((self.work / "target").exists())

    def test_a_file_named_like_a_private_directory_is_left_alone(self):
        (self.work / "target").write_text("a file")
        r = self.ai("-w")
        self.assertOk(r)
        self.assertNotIn(f"{self.MOUNT}/target", r.bwrap.targets())

    def test_symlinks_are_not_mount_points(self):
        for name in (".envrc", "target"):
            (self.work / name).symlink_to("/nonexistent")
        r = self.ai("-w")
        self.assertOk(r)
        self.assertNotIn(f"{self.MOUNT}/.envrc", r.bwrap.targets())
        self.assertNotIn(f"{self.MOUNT}/target", r.bwrap.targets())

    def test_what_is_in_a_git_dir_is_not_a_tool_file(self):
        self.make_repo()
        (self.work / ".git/.envrc").write_text("x")
        r = self.ai("-w", answers=["y"])
        self.assertNotIn(f"{self.MOUNT}/.git/.envrc", r.bwrap.targets())


class ReadOnlyPatterns(AiTestCase):
    MOUNT = "/work/software/proj"

    def setUp(self):
        super().setUp()
        (self.work / "docs/deep").mkdir(parents=True)
        for rel in ("a.pdf", "docs/b.pdf", "docs/deep/c.pdf", "docs/d.txt"):
            (self.work / rel).write_text("x")

    def ro(self, *patterns, **kwargs):
        r = self.ai("-w", *[a for p in patterns for a in ("--ro", p)], **kwargs)
        self.assertOk(r)
        return sorted(t[len(self.MOUNT) + 1:] for t in r.bwrap.targets("--ro-bind")
                      if t.startswith(self.MOUNT + "/"))

    def test_a_name_matches_anywhere(self):
        self.assertEqual(self.ro("*.pdf"), ["a.pdf", "docs/b.pdf", "docs/deep/c.pdf"])

    def test_a_path_matches_from_the_work_dir(self):
        self.assertEqual(self.ro("docs/d.txt"), ["docs/d.txt"])
        self.assertEqual(self.ro("docs"), ["docs"])

    def test_star_also_matches_slashes_in_a_path(self):
        self.assertEqual(self.ro("docs/*.pdf"), ["docs/b.pdf", "docs/deep/c.pdf"])

    def test_a_trailing_slash_or_a_leading_dot_changes_nothing(self):
        self.assertEqual(self.ro("docs/"), ["docs"])
        self.assertEqual(self.ro("./docs/d.txt"), ["docs/d.txt"])

    def test_the_equals_form(self):
        (self.work / "-x").write_text("x")
        r = self.ai("-w", "--ro=-x")
        self.assertIn(f"{self.MOUNT}/-x", r.bwrap.targets("--ro-bind"))

    def test_several_patterns_after_one_option(self):
        r = self.ai("-w", "--ro", "a.pdf", "docs/d.txt", "--", "tool")
        self.assertOk(r)
        targets = r.bwrap.targets("--ro-bind")
        self.assertIn(f"{self.MOUNT}/a.pdf", targets)
        self.assertIn(f"{self.MOUNT}/docs/d.txt", targets)
        self.assertEqual(r.bwrap.cmd, ["tool"])

    def test_the_whole_work_dir(self):
        r = self.ai("-w", "--ro", ".")
        self.assertIn(("--ro-bind", str(self.work), self.MOUNT), r.bwrap.mounts())

    def test_an_absolute_path_in_the_work_dir(self):
        self.assertEqual(self.ro(f"{self.work}/docs/d.txt"), ["docs/d.txt"])

    def test_an_absolute_path_outside_the_work_dir(self):
        self.assertRefused(self.ai("-w", "--ro", "/etc/passwd"), "is not in the work dir")

    def test_a_sibling_with_the_same_prefix_is_outside(self):
        self.assertRefused(self.ai("-w", "--ro", f"{self.work}2/x"), "is not in the work dir")

    def test_no_match_is_reported(self):
        r = self.ai("-w", "--ro", "*.nothing")
        self.assertOk(r)
        self.assertIn("nothing matches", r.err)

    def test_symlinks_are_skipped(self):
        (self.work / "link.pdf").symlink_to("/etc/passwd")
        r = self.ai("-w", "--ro", "*.pdf")
        self.assertIn("symlink skipped", r.err)
        self.assertNotIn(f"{self.MOUNT}/link.pdf", r.bwrap.targets())

    def test_git_and_private_directories_are_skipped(self):
        self.make_repo()
        (self.work / "target").mkdir()
        (self.work / "target/x.pdf").write_text("x")
        (self.work / ".git/x.pdf").write_text("x")
        r = self.ai("-w", "--ro", "*.pdf", answers=["y"])
        targets = r.bwrap.targets("--ro-bind")
        self.assertNotIn(f"{self.MOUNT}/target/x.pdf", targets)
        self.assertNotIn(f"{self.MOUNT}/.git/x.pdf", targets)

    def test_clones_and_private_copies_are_mounted_over_read_only_binds(self):
        # A read-only bind mounted after them would cover them, and expose
        # what they hide.
        self.make_repo(self.work / "sub")
        (self.work / "sub/target").mkdir()
        (self.work / "sub/.envrc").write_text("x")
        r = self.ai("-w", "--ro", "sub", answers=["y"])
        targets = r.bwrap.targets()
        ro = targets.index(f"{self.MOUNT}/sub")
        for inner in (".git", "target", ".envrc"):
            self.assertGreater(targets.index(f"{self.MOUNT}/sub/{inner}"), ro, inner)

    def test_the_whole_work_dir_read_only_comes_before_what_it_would_cover(self):
        self.make_repo()
        (self.work / "Cargo.toml").write_text("x")
        r = self.ai("-w", "--ro", ".", answers=["y"])
        targets = r.bwrap.targets()
        first = targets.index(self.MOUNT, targets.index(self.MOUNT) + 1)
        self.assertGreater(targets.index(f"{self.MOUNT}/.git"), first)
        self.assertGreater(targets.index(f"{self.MOUNT}/target"), first)


class Warnings(AiTestCase):
    """What must be confirmed before the session."""

    def test_a_hard_link_outside(self):
        (self.outside / "f").write_text("x")
        os.link(self.outside / "f", self.work / "linked")
        r = self.ai("-w")
        self.assertRefused(r, "Hard-linked outside the work dir")
        self.assertShownPath(r, "linked")
        self.assertOk(self.ai("-w", answers=["y"]))

    def assertShownPath(self, r, name):
        self.assertIn(str(self.work / name), r.err)

    def test_a_hard_link_inside_is_fine(self):
        (self.work / "f").write_text("x")
        os.link(self.work / "f", self.work / "g")
        r = self.ai("-w")
        self.assertOk(r)
        self.assertNotIn("Hard-linked", r.err)

    def test_a_hard_link_to_a_read_only_file(self):
        (self.work / "protected").write_text("x")
        os.link(self.work / "protected", self.work / "writable")
        r = self.ai("-w", "--ro", "protected")
        self.assertRefused(r, "same file as read-only")

    def test_a_hard_link_between_read_only_files_is_fine(self):
        (self.work / "a").write_text("x")
        os.link(self.work / "a", self.work / "b")
        r = self.ai("-w", "--ro", "a", "b")
        self.assertOk(r)
        self.assertNotIn("Hard-linked", r.err)

    def test_a_hard_link_in_what_the_sandbox_does_not_see(self):
        (self.work / "target").mkdir()
        (self.outside / "f").write_text("x")
        os.link(self.outside / "f", self.work / "target/linked")
        r = self.ai("-w")
        self.assertOk(r)
        self.assertNotIn("Hard-linked", r.err)

    def test_a_named_pipe(self):
        os.mkfifo(self.work / "pipe")
        r = self.ai("-w")
        self.assertRefused(r, "Unix sockets or named pipes")
        self.assertOk(self.ai("-w", answers=["y"]))

    def test_a_socket(self):
        import socket
        # Short path: a socket's is limited.
        old = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, old)
        with socket.socket(socket.AF_UNIX) as s:
            s.bind("sock")
        self.assertRefused(self.ai("-w"), "Unix sockets or named pipes")

    def test_python_bytecode_warning(self):
        (self.work / "x.py").write_text("")
        r = self.ai("-w", env={"PYTHONPYCACHEPREFIX": None})
        self.assertIn("PYTHONPYCACHEPREFIX", r.err)
        for bad in ("relative", str(self.work / "cache"), str(self.base / "cache")):
            with self.subTest(value=bad):
                r = self.ai("-w", env={"PYTHONPYCACHEPREFIX": bad})
                self.assertIn("PYTHONPYCACHEPREFIX", r.err)

    def test_no_python_bytecode_warning(self):
        (self.work / "x.py").write_text("")
        self.assertNotIn("PYTHONPYCACHEPREFIX", self.ai("-w").err)
        (self.work / "x.py").unlink()
        self.assertNotIn("PYTHONPYCACHEPREFIX",
                         self.ai("-w", env={"PYTHONPYCACHEPREFIX": None}).err)


if __name__ == "__main__":
    unittest.main()
