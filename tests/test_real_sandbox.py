"""The real sandbox: run on the host, with AI_TEST_REAL=1.

The other tests fake bwrap.  These start real sessions, and check from
inside what the agent can do.  They can't run inside `ai` itself: it
forbids user namespaces and mounts there.
"""
import os
import unittest
from pathlib import Path

from harness import AiTestCase, STUBS, q

REAL = os.environ.get("AI_TEST_REAL")


@unittest.skipUnless(REAL, "needs a real sandbox: run on the host with AI_TEST_REAL=1")
class RealSandbox(AiTestCase):
    fake_sandbox = False
    MOUNT = "/work/software/proj"

    def environment(self, extra=None):
        # Real tools, but no notification on your desktop.
        notify = self.root / "bin"
        notify.mkdir(exist_ok=True)
        if not (notify / "notify-send").exists():
            (notify / "notify-send").symlink_to(STUBS / "notify-send")
        env = super().environment(extra)
        env["PATH"] = f"{notify}:{env['PATH']}"
        return env

    def sandboxed(self, script, *options, **kwargs):
        """Run `script` in a session; its output is in the result."""
        return self.ai("-w", *options, "--", "/bin/bash", "-c", script, **kwargs)

    def assertDenied(self, script, *options, **kwargs):
        r = self.sandboxed(script, *options, **kwargs)
        self.assertNotEqual(r.rc, 0, f"allowed in the sandbox: {script}\n{r!r}")
        return r

    def test_the_session_runs_in_the_mount(self):
        r = self.sandboxed("pwd; echo $AI_PROJECT; echo $HOME")
        self.assertOk(r)
        self.assertEqual(r.out.split(), [self.MOUNT, self.MOUNT, str(self.home)])

    def test_the_work_dir_is_writable(self):
        self.assertOk(self.sandboxed("echo hi >file"))
        self.assertEqual((self.work / "file").read_text(), "hi\n")

    def test_your_files_are_invisible(self):
        secret = self.home / "secret"
        secret.write_text("x")
        self.assertDenied(f"test -e {secret}")
        self.assertDenied(f"cat {self.home}/AI/config")

    def test_the_system_is_read_only(self):
        self.assertDenied("touch /usr/x")
        for path in ("/etc/hosts", "/etc/passwd", "/etc/resolv.conf", "/etc/ssl/x"):
            self.assertDenied(f"touch {path}")

    def test_the_host_etc_is_not_there(self):
        # /etc itself is a directory of the sandbox's own root: what is written
        # there is lost with the session, and only the files ai mounts are seen.
        self.assertDenied("test -e /etc/shadow")
        self.assertOk(self.sandboxed("touch /etc/x"))
        self.assertDenied("test -e /etc/x")

    def test_your_etc_files_are_read_only(self):
        etc = self.home / "AI/etc/claude-code"
        etc.mkdir(parents=True)
        (etc / "CLAUDE.md").write_text("Never push.\n")
        r = self.sandboxed("cat /etc/claude-code/CLAUDE.md")
        self.assertOk(r)
        self.assertEqual(r.out, "Never push.\n")
        for script in ("echo x >>/etc/claude-code/CLAUDE.md", "touch /etc/claude-code/x",
                       "mv /etc/claude-code /etc/y", "rm -r /etc/claude-code"):
            self.assertDenied(script)
        self.assertEqual(sorted(os.listdir(etc)), ["CLAUDE.md"])

    def test_the_host_filesystem_is_not_reachable_through_proc(self):
        # Each process seen has the sandbox's root, where $HOME is the agent's.
        secret = self.home / "secret"
        secret.write_text("x")
        self.assertOk(self.sandboxed("test -d /proc/1/root/usr"))  # the way works
        self.assertDenied(f'for p in /proc/[0-9]*/root; do test -e "$p"{q(str(secret))} && exit 0; done; exit 1')

    def test_the_environment_is_cleared(self):
        r = self.sandboxed("env", env={"HOST_SECRET": "hunter2"})
        self.assertNotIn("hunter2", r.out)

    def test_secrets_do_not_show_in_the_process_list(self):
        self.write_config("AI_ENV=(TOKEN=hunter2)")
        r = self.sandboxed('echo "$TOKEN"; cat /proc/*/cmdline 2>/dev/null | tr "\\0" " "')
        self.assertEqual(r.out.split("\n")[0], "hunter2")
        self.assertNotIn("hunter2", r.out.split("\n", 1)[1])

    def test_read_only_patterns(self):
        (self.work / "data").write_text("x")
        self.assertDenied("echo y >data", "--ro", "data")
        self.assertEqual((self.work / "data").read_text(), "x")

    def test_read_only_work_dir(self):
        self.assertDenied("touch new", "--ro", ".")

    def test_tool_files_are_read_only(self):
        (self.work / ".envrc").write_text("x")
        self.assertDenied("echo evil >.envrc")
        self.assertDenied("rm .envrc")
        self.assertEqual((self.work / ".envrc").read_text(), "x")

    def test_private_directories_stay_yours(self):
        (self.work / "target").mkdir()
        (self.work / "target/yours").write_text("x")
        self.assertOk(self.sandboxed("test ! -e target/yours; touch target/agent"))
        self.assertEqual(os.listdir(self.work / "target"), ["yours"])
        self.assertTrue((self.project_dir() / "shadow/target/agent").exists())

    def test_the_real_repo_is_protected(self):
        self.make_repo()
        r = self.sandboxed(
            "git -c user.name=a -c user.email=a@a commit -q --allow-empty -m agent;"
            "git config core.fsmonitor evil; touch .git/hooks/pre-commit; git log --oneline",
            answers=["y"])
        self.assertOk(r)
        self.assertIn("agent", r.out)
        self.assertNotIn("agent", self.git("log", "--oneline"))
        self.assertNotIn("fsmonitor", (self.work / ".git/config").read_text())
        self.assertFalse((self.work / ".git/hooks/pre-commit").exists())

    def test_upstream_is_read_only_and_can_be_fetched(self):
        self.make_repo()
        self.assertDenied("touch /upstream/.git/x", answers=["y"])
        self.assertOk(self.sandboxed("git fetch -q origin"))

    def test_commits_can_be_brought_back(self):
        self.make_repo()
        self.assertOk(self.sandboxed(
            "git -c user.name=a -c user.email=a@a commit -q --allow-empty -m agent", answers=["y"]))
        self.git("fetch", "-q", "ai")
        self.assertIn("agent", self.git("log", "--oneline", "ai/main"))

    def test_home_changes_stay_in_the_project(self):
        (self.base / "shared").write_text("base\n")
        other = self.home / "other"
        other.mkdir()
        self.assertOk(self.sandboxed('echo project >~/shared; echo new >~/new; test "$(cat ~/shared)" = project'))
        self.assertEqual((self.base / "shared").read_text(), "base\n")
        self.assertFalse((self.base / "new").exists())
        self.assertEqual((self.project_dir() / "home/shared").read_text(), "project\n")
        r = self.ai("-w", "--", "/bin/bash", "-c", "cat ~/shared; test ! -e ~/new", cwd=other)
        self.assertOk(r)
        self.assertEqual(r.out, "base\n")

    def test_a_deletion_can_be_promoted(self):
        (self.base / "gone").write_text("x")
        self.assertOk(self.sandboxed("rm ~/gone"))
        self.assertOk(self.ai("--promote", "gone", answers=["y"]))
        self.assertFalse((self.base / "gone").exists())

    def test_a_directory_replaced_as_a_whole_cannot_be_promoted(self):
        (self.base / "d").mkdir()
        (self.base / "d/old").write_text("x")
        self.assertOk(self.sandboxed("rm -r ~/d; mkdir ~/d; touch ~/d/new"))
        self.assertRefused(self.ai("--promote", "d/new", answers=["y"]), "as a whole")

    def test_namespaces_cannot_be_created(self):
        self.assertDenied("unshare -U true")

    def test_the_seccomp_filter_blocks(self):
        code = ("import ctypes,sys;l=ctypes.CDLL(None,use_errno=True);"
                f"r=l.syscall({{}});sys.exit(0 if r==-1 else 1)")
        for name, call in (("io_uring_setup", "425,0,0"), ("bpf", "321,0,0,0"),
                           ("userfaultfd", "323,0"), ("perf_event_open", "298,0,0,0,0,0"),
                           ("keyctl", "250,0,0,0,0,0"), ("mount", "165,0,0,0,0,0")):
            with self.subTest(syscall=name):
                self.assertOk(self.sandboxed(f"python3 -c '{code.format(call)}'"))

    def test_typing_into_the_terminal_is_blocked(self):
        code = "import fcntl,sys;fcntl.ioctl(0,0x5412,b\"x\")"
        self.assertDenied(f"python3 -c '{code}'")

    def test_only_some_socket_families(self):
        self.assertDenied("""python3 -c 'import socket; socket.socket(socket.AF_PACKET, socket.SOCK_RAW)'""")
        self.assertOk(self.sandboxed("""python3 -c 'import socket; socket.socket(socket.AF_INET)'"""))

    def test_the_local_network_is_rejected(self):
        for address in ("192.168.0.1", "10.0.2.2", "169.254.169.254"):
            with self.subTest(address=address):
                self.assertDenied(
                    "python3 -c 'import socket; s=socket.socket();"
                    f"s.settimeout(3); s.connect((\"{address}\", 80))'")

    def test_the_firewall_cannot_be_changed(self):
        self.assertDenied("nft flush ruleset || /usr/sbin/nft flush ruleset")

    def test_an_event_reaches_the_desktop(self):
        r = self.sandboxed('echo stop >"$AI_NOTIFY"; sleep 0.3')
        self.assertOk(r)
        self.assertEqual(len(r.notifications), 1, r.notifications)

    def test_a_new_tool_file_is_reported(self):
        r = self.sandboxed("touch .envrc; sleep 0.3")
        self.assertTrue(any(".envrc" in n[-1] for n in r.notifications), r.notifications)
        self.assertIn(f"{self.work}/.envrc", r.err)


if __name__ == "__main__":
    unittest.main()
