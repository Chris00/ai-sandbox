# Tests of `ai`

They treat `ai` as a black box: its output, the arguments it gives to
bwrap and what it leaves on disk.  They don't depend on its language, so
they can check a rewrite against the same expectations.

```sh
tests/run.py                        # all, in parallel, but the real sandbox
tests/run.py Promote test_cli       # the classes with these in their name
AI_TEST_SHOW_KNOWN_BUGS=1 tests/run.py test_names
AI_TEST_REAL=1 tests/run.py RealSandbox   # on the host
cd tests && python3 -m unittest test_names.Evasion -v     # one at a time
```

`tests/run.py` takes about a minute with 24 CPUs, and ten or so on one:
most of the time goes to `test_names.py`, which has about 950 cases.

Nothing to install but what `ai` needs.  Each test has its
own `$HOME` in a temporary directory; your real one isn't used, and the
fake notify-send keeps your desktop quiet.

| File | Tests |
|---|---|
| `test_cli.py` | options, limits, configuration, refused directories, locks |
| `test_session.py` | the arguments of bwrap: isolation, environment, mounts and their order, Git clones, tool files, `--ro`, warnings |
| `test_reports.py` | the report at exit, the log, desktop notifications, the agent's events |
| `test_projects.py` | `--status`, `--list`, `--promote`, `--reset`, `--forget` |
| `test_names.py` | hostile names, everywhere names are handled or shown |
| `test_real_sandbox.py` | what the agent really can do in a session (on the host) |
| `test_harness.py` | the fake sandbox itself |

`ai` runs on a terminal of its own, under `session_leader.py`, which
stands for your shell: if `ai` led the session, the kernel would hang up
whatever it leaves behind at its exit, and that would go unnoticed.

## The fake sandbox

A sandbox can't start inside the sandbox, and most of what `ai` does
doesn't need one.  `stubs/bwrap`, set as `BWRAP` in the test's
configuration, records what it is asked and follows bwrap's protocol
(`--info-fd`, `--block-fd`, `--args`), so that the network setup of `ai`
really runs.  Then it runs a test's "session", as shell code on the
host, with `WORK`, `LAYER`, `BASE` and `NOTIFY` set to the host paths of
what the agent would get: it stands for what the agent does.

These tests thus can't tell whether the sandbox actually holds: they
check what `ai` asks for.  That is the job of `test_real_sandbox.py`.

Only these are fake: bwrap, nft, slirp4netns, ssh-agent, ssh-add,
notify-send, systemctl, systemd-run, and `python3` for `JOIN_NS` only.
Git, inotifywait, flock, the seccomp filter... are the real ones.

## Known bugs

A test failing because of a bug in `ai` is marked `known_bug`, with the
cause, or listed with `for_each_name(known...)`.  The suite stays green
(they count as "expected failures") and turns red when the bug is fixed
("unexpected success"), so that the mark gets removed.

## Porting `ai` to another language

Set `AI_TEST_PROGRAM` to the new program.  Two things depend on the
implementation:

- `AiTestCase.write_config` in `harness.py`, and the tests that give it
  lines of configuration, are in bash;
- `stubs/python3` recognizes `JOIN_NS` by its text.

Messages are compared as they are: a rewrite should keep them, or change
the tests knowingly.

`seccomp.bpf`, in a run's log directory, holds the filter bwrap got: the
one of a rewrite can be compared byte by byte with the original's.

## Not covered

- `migrate_old_layout`: to be removed anyway.
- File systems mounted inside the work dir, which need privileges.
- The desktop notifications' look, the colours on a terminal, signals
  other than SIGKILL.
