"""The Python version against the bash one, kept as a reference.

The same scenarios, in the same paths, must give the same exit statuses,
output, arguments to bwrap, seccomp filter, notifications and log.  The
tests of the other files say what is expected; these catch what they
don't think of.
"""
import json
import os
import re
import shutil
import unittest

from harness import AI, IS_BASH, STUBS, AiTestCase, q, render_bash, render_python

REFERENCE = AI.parent / "ai.bash"


def normalize(text):
    text = re.sub(r"\bai\.[A-Za-z0-9_]{8}\b", "ai.TMP", text)
    return re.sub(r"\d{4}-\d\d-\d\d \d\d:\d\d(:\d\d)?", "DATE", text)


@unittest.skipIf(IS_BASH or not REFERENCE.exists(), "compares the Python version to the bash one")
class Reference(AiTestCase):
    def write_both_configs(self, add=None, **settings):
        settings.setdefault("BWRAP", str(STUBS / "bwrap"))
        # Unused by the bash version, which runs python3 from the PATH.
        settings.setdefault("PYTHON", str(STUBS / "python3"))
        for render, name in ((render_bash, "config"), (render_python, "config.py")):
            text = "".join(render(k, v, False) for k, v in settings.items())
            text += "".join(render(k, v, True) for k, v in (add or {}).items())
            path = self.home / "AI" / name
            path.write_text(text)
            path.chmod(0o600)

    def fresh(self, settings):
        """The same empty home again, at the same place."""
        self._stop_all()
        self._running = []
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.root / "log", ignore_errors=True)
        for d in (self.base, self.work):
            d.mkdir(parents=True)
        self.write_both_configs(**settings)

    def observe(self, result):
        def read(name):
            path = result.log / name
            return path.read_bytes() if path.exists() else None
        seen = dict(rc=result.rc, out=normalize(result.out), err=normalize(result.err),
                    notifications=[[normalize(a) for a in n] for n in result.notifications],
                    seccomp=read("seccomp.bpf"), nft=read("nft.rules"), slirp=None)
        if read("slirp.args"):
            seen["slirp"] = re.sub(rb"--ready-fd=\d+ |/proc/\d+", b"", read("slirp.args"))
        if result.bwrap:
            seen["ops"] = json.loads(normalize(json.dumps(result.bwrap.ops)))
            seen["cmd"] = result.bwrap.cmd
        return seen

    def compare(self, scenario, **settings):
        results = []
        for program in (REFERENCE, AI):
            self.fresh(settings)
            steps = []

            def run(*args, **kwargs):
                r = self.ai(*args, program=program, **kwargs)
                steps.append(self.observe(r))
                return r
            scenario(run)
            log = self.warnings.read_bytes() if self.warnings.exists() else b""
            results.append(dict(steps=steps, log=normalize(log.decode("utf-8", "replace"))))
        self.assertEqual(results[0], results[1])

    def test_a_shell_in_a_project(self):
        self.compare(lambda run: run("-w"))

    def test_the_base_home(self):
        self.compare(lambda run: run())

    def test_settings(self):
        self.compare(lambda run: run("-w"), AI_ENV=["A=1", "B=x y"], AI_PROJECT_ENV=["C=2"],
                     FORBID_WORK=[f"{self.home}/x"])

    def test_a_command(self):
        self.compare(lambda run: run("-w", "--", "tool", "a b", "--help", "*"))

    def test_refusals(self):
        def scenario(run):
            run("--bogus")
            run("stray")
            run("-w", "--")
            run("--ro", "x")
            run("--promote")
            run("--list", "-w")
            run("-w", "--ro", "/etc")
            run("-w", cwd=self.home)
            run("-w", cwd="/")
            run("--ssh")
            run("--status")
            run("--reset")
            run("--forget")
            run("--forget", "/none")
        self.compare(scenario)

    def test_tool_files_and_read_only_patterns(self):
        def scenario(run):
            w = self.work
            for name in (".envrc", ".npmrc", "a.pdf", "b.pdf"):
                (w / name).touch()
            for name in ("sub/.vscode", "target", "sub/venv", "docs", "keep"):
                (w / name).mkdir(parents=True)
            (w / "Cargo.toml").touch()
            (w / "docs/x.pdf").touch()
            (w / "sub/.npmrc").touch()
            (w / "venv").touch()                # not a directory: left alone
            (w / "link").symlink_to("a.pdf")
            run("-w", "--ro", "*.pdf", "docs/*", "keep", "link", "nothing", "--ro=sub/")
            run("-w", "--ro", ".")
            run("--status")
        self.compare(scenario)

    def test_many_read_only_entries(self):
        def scenario(run):
            for i in range(5):
                (self.work / f"{i}.txt").touch()
            run("-w", "--ro", "*.txt")
        self.compare(scenario)

    def test_repos(self):
        def scenario(run):
            self.make_repo()
            self.make_repo(self.work / "nested/inner")
            (self.work / "sub").mkdir()
            (self.work / "sub/.git").write_text("gitdir: /outside/x\n")
            (self.work / "in").mkdir()
            (self.work / "in/.git").write_text(f"gitdir: {self.work}/data\n")
            self.git("config", "core.hooksPath", ".husky")
            run("-w")                           # refused: not trusted
            run("-w", answers=["y"])            # the link, then one repo...
            run("-w", answers=["y", "y", "y"])
            run("-w", answers=["y"], session='cd "$WORK"; touch new')
            run("--status")
            run("--list")
            run("--forget", answers=["n"])
            run("--forget", answers=["y"])
            run("--list")
        self.compare(scenario)

    def test_warnings(self):
        def scenario(run):
            (self.outside / "f").touch()
            os.link(self.outside / "f", self.work / "linked")
            (self.work / "a").touch()
            os.link(self.work / "a", self.work / "b")
            os.mkfifo(self.work / "pipe")
            (self.work / "x.py").touch()
            env = dict(AI_TEST_MOUNTS=f"/\n{self.work}/disk", PYTHONPYCACHEPREFIX="rel")
            run("-w", env=env)
            run("-w", env=env, answers=["y"])
            run("-w", env=env, answers=["y", "y", "y"])
            (self.work / "linked").unlink()     # two warnings: in no given order
            run("-w", "--ro", "a", answers=["y", "y", "y"])
        self.compare(scenario)

    def test_what_a_session_reports(self):
        session = '''
            cd "$WORK"
            touch .envrc; mkdir -p sub/deep; touch sub/deep/.npmrc; git init -q new
            ln -s /etc/passwd out; ln -s inside in
            mkdir target2; touch $'odd\\nname'
            mkdir -p "$LAYER/.claude/skills/s" "$LAYER/.local/bin" "$LAYER/.cache" "$LAYER/a/b"
            touch "$LAYER/.bashrc" "$LAYER/.claude/skills/s/f" "$LAYER/.claude/skills/s/g"
            touch "$LAYER/.local/bin/tool" "$LAYER/.cache/c" "$LAYER/a/b/c" "$LAYER/a/b/d" "$LAYER/plain"
            sleep 0.3
            exit 5'''

        def scenario(run):
            run("-w", session=session)
            run("--status")
            run("-w", session="true")
        self.compare(scenario)

    def test_the_base_home_report(self):
        def scenario(run):
            run(session='touch "$BASE/.bashrc" "$BASE/notes"; mkdir -p "$BASE/.claude/hooks"; '
                        'touch "$BASE/.claude/hooks/h"')
        self.compare(scenario)

    def test_agent_events(self):
        def scenario(run):
            run("-w", session='(echo stop; echo bogus; echo input; echo stop) >"$NOTIFY"; sleep 0.3')
            run(session='echo stop >"$NOTIFY"; sleep 0.3')
        self.compare(scenario)

    def test_promote(self):
        def scenario(run):
            (self.base / "old").write_text("base\n")
            (self.base / "gone").write_text("base\n")
            run("-w", session='''
                printf 'project\\n' >"$LAYER/old"; printf 'new\\n' >"$LAYER/new"
                ln -s target "$LAYER/link"; mkdir "$LAYER/dir"; echo x >"$LAYER/dir/f"''')
            run("--status")
            run("--promote", "none")
            run("--promote", "/etc/x")
            run("--promote", "../x")
            run("--promote", "~/old", "new", "link", "dir", answers=["n"])
            run("--promote", "~/old", "new", "link", "dir", answers=["y"])
            run("--status")
            run("--reset", answers=["n"])
            run("--reset", answers=["y"])
        self.compare(scenario)

    def test_ssh(self):
        def scenario(run):
            (self.home / ".ssh").mkdir()
            (self.home / ".ssh/id_ed25519_ai").write_text("key")
            run("-w", "--ssh", session="sleep 0.05")
        self.compare(scenario, AI_SSH_HOSTS=["example.org"])

    def test_hostile_names(self):
        def scenario(run):
            for name in ("a b", "a\tb", "a\nb", "ab\n", os.fsdecode(b"a\xffb"), "é€", "a*b",
                         "-rf", "a\x1b[31mb", "a\u009bb", "<b>&"):
                d = self.work / name
                d.mkdir()
                (d / ".envrc").touch()
                (d / "f").touch()
                (d / "link").symlink_to("/etc/passwd")
            names = " ".join(q(n) for n in ("n\nm", os.fsdecode(b"n\xffm"), "n\x1b[31m", "n\tm"))
            run("-w", "--ro", "f", session=f'''
                cd "$WORK"; for n in {names}; do
                    mkdir "$n"; touch "$n/.npmrc"; ln -s /x/"$n" "lnk$n"; touch "$LAYER/$n"
                done; sleep 0.3''')
            run("--status")
        self.compare(scenario)


if __name__ == "__main__":
    unittest.main()
