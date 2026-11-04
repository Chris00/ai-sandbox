# Tests of `ai`

They treat `ai` as a black box: its output, the arguments it gives to
bwrap and what it leaves on disk.  They don't depend on its language, so
they can check a rewrite against the same expectations.

```sh
python3 -m unittest discover -s tests           # all but the real sandbox
python3 -m unittest discover -s tests -k Promote    # some of them
AI_TEST_SHOW_KNOWN_BUGS=1 python3 -m unittest discover -s tests
AI_TEST_REAL=1 python3 -m unittest tests/test_real_sandbox.py     # on the host
```

Nothing to install but what `ai` needs, plus `pgrep`.  Each test has its
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
