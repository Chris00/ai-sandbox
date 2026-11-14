# Threat model

What `ai` promises, how each promise is kept, and which test shows it.
Each promise is something to attack: when a change touches mounts,
checks or reports, go through the promises it concerns and the
[checklist](#checklist) below, and add a real-sandbox test for any
attack that matters.

## The adversary

The agent, and whatever steers it: a web page, a dependency, a file in
the project can inject instructions (prompt injection). Assume it is
hostile, runs any code it likes as your user inside the sandbox, knows
how `ai` works, and is patient: it can prepare something in one session
for a later one, or for when you next work in the project.

Trusted: you, your machine and what runs on it outside the sandbox, the
Linux kernel, bubblewrap, slirp4netns, nftables, Git, and your
configuration (`~/AI/config.py`).

## What is protected

- Your files outside the work directory, your SSH keys, your session
  (environment, terminal, desktop).
- Your local network and the services of your machine.
- In the work directory: your Git history and configuration, your
  build output and environments, the settings that make your tools run
  code, and what `--ro` names.
- Your machine afterwards: what the agent leaves that you, or your
  tools, will later run.
- Each project from the others, and from what an agent did in them.

## Accepted risks

See [Limitations](../README.md#limitations) in the README. In short:
whatever the sandbox can read can be sent to the internet (including
through DNS queries to slirp4netns's forwarder); what the agent writes
in the work tree, its home and its projects' directories is untrusted;
the kernel is trusted; disk space isn't limited; public addresses are
reachable; agents run outside the sandbox follow what it wrote.

## Promises

The tests are in `tests/`. A real-sandbox test (`test_real_sandbox.py`)
attacks the promise; the others check what `ai` asks of bubblewrap, or
what it does on the host, with a fake sandbox.

### The sandbox itself

| Promise | Kept by | Tests |
|---|---|---|
| Nothing of your home is visible but what `ai` mounts | bwrap: an empty root, only listed binds | real: `test_your_files_are_invisible`, `test_the_host_filesystem_is_not_reachable_through_proc`; `test_session.Isolation.test_only_expected_paths_are_visible` |
| The system is read-only | `--ro-bind` of `/usr`, `/lib`, `/bin`…; a few files of `/etc` | real: `test_the_system_is_read_only`, `test_the_host_etc_is_not_there` |
| Your files in `~/AI/etc` can't be changed | `--ro-bind` at `/etc/NAME`, outside every overlay | real: `test_your_etc_files_are_read_only` |
| No variable of your environment comes in | `--clearenv`, before any `--setenv` | real: `test_the_environment_is_cleared`; `test_session.Isolation` |
| `AI_ENV`'s values (secrets) aren't on any command line | given to bwrap with `--args`, from a memory file | real: `test_secrets_do_not_show_in_the_process_list`; `test_session.Environment.test_secrets_stay_off_the_command_line` |
| No new user namespace, so no capabilities | `--unshare-user --disable-userns` | real: `test_namespaces_cannot_be_created` |
| Kernel interfaces with a record of exploited bugs are unreachable | seccomp filter (io_uring, bpf, userfaultfd, keyrings, mount API, socket families, 32-bit calls) | real: `test_the_seccomp_filter_blocks`, `test_only_some_socket_families` |
| The agent can't type into your terminal | `--new-session`, and TIOCSTI and TIOCLINUX blocked | real: `test_typing_into_the_terminal_is_blocked` |
| Nothing on your local network or your machine is reachable | own network namespace, slirp4netns with `--disable-host-loopback`, nftables rules | real: `test_the_local_network_is_rejected`; `test_session.Network` |
| The firewall can't be changed | rules installed as root of a user namespace the sandbox isn't in | real: `test_the_firewall_cannot_be_changed` |
| No session without the firewall | bwrap waits on `--block-fd` until the network is set up; killed otherwise | `test_session.Network.test_no_session_without_the_firewall`, `test_no_session_without_slirp4netns` |
| The SSH key is usable only on `AI_SSH_HOSTS`, and never readable | a private ssh-agent, loaded with destination constraints; only its socket is mounted | `test_session.WorkDir.test_ssh_agent` (**no real test**) |
| CPU, memory and processes are limited | a systemd scope | `test_cli.Limits` (**no real test**) |
| The sandbox doesn't outlive `ai` | `--die-with-parent`; `ai` stops it on a signal | `test_session_end.Signals` |

### The work directory

| Promise | Kept by | Tests |
|---|---|---|
| Only the work directory is mounted, and never your home, its hidden directories, `/` or `ai`'s own files | `check_work_path`, on the realpath | `test_cli.Refused` |
| Your Git history, configuration and hooks can't be changed | each `.git` hidden behind a clone; yours read-only under `/upstream` | real: `test_the_real_repo_is_protected`, `test_upstream_is_read_only_and_can_be_fetched`; `test_session.Repos` |
| A repo the agent created isn't trusted | a repo without a clone needs your word | `test_session.Repos.test_a_repo_created_by_the_agent_is_not_trusted` |
| Host-side Git run by `ai` runs no code from the repo | `core.hooksPath=/dev/null`, `core.fsmonitor=false`, the config written with `git config --file`, no `git remote remove` | `test_session.Repos.test_no_hook_of_your_repo_runs`, `test_projects.Forget.test_no_hook_of_your_repo_runs*`, `test_projects.Status.test_status_does_not_run_what_the_agent_left_in_the_clone` |
| `ai` never writes through a link in a repo's config | refuses a config that is a symlink or has several names | `test_session.Repos.test_a_symlinked_config_is_refused`, `test_a_hard_linked_config_is_refused` |
| `READONLY` entries can't be changed | `--ro-bind` | real: `test_tool_files_are_read_only` |
| `PRIVATE` directories stay yours | a private copy mounted over them | real: `test_private_directories_stay_yours` |
| What `--ro` names can't be changed | `--ro-bind`, before the clones and private copies | real: `test_read_only_patterns`, `test_read_only_work_dir`; `test_session.ReadOnlyPatterns` |
| None of these can be replaced by renaming a parent | each directory leading to a protected entry is bound onto itself: a mount point can't be renamed or removed | real: `test_a_protected_entry_cannot_be_replaced_through_its_parent`; `test_session.ToolFiles.test_the_parents_of_protected_entries_are_pinned` |
| Nothing lets the sandbox write outside the work directory, or reach a program on the host | asks first about hard links to outside or to read-only files, inner mounts, sockets and named pipes | `test_session.Warnings`, `test_session_end.Mounts` (**no real test**) |

### Reports

| Promise | Kept by | Tests |
|---|---|---|
| A new tool file, or a new repo, is notified as it appears | inotifywait on the work dir; a new directory is searched, since what it holds before it is watched is missed | real: `test_a_new_tool_file_is_reported`; `test_reports.LiveNotifications`, `test_names.LiveNotifications`, `test_names.Evasion` |
| At exit, every new or changed tool file, repo config or hook, and new symlink out of the work dir, is reported | snapshots before and after, by ctime, which the sandbox can't set back; protected paths are skipped, since they can't change | `test_reports.ExitReport`, `test_names.ExitReport`, `test_names.Evasion` |
| Changes to the agent's home that run code or steer the agent are reported, in a project and in the base home | snapshots of the layer (in a project) or of `HOME_TRIGGERS` (in the base home) | `test_reports.HomeReport`, `test_names.ExitReport.test_home_change*` |
| Nothing the agent names can drive your terminal or forge a line of a report | control characters replaced by `?`; names read NUL-separated, never parsed from text | `test_names` (every case, with about 950 hostile names) |
| A notification's text is never the agent's | the agent writes one of `AGENT_EVENTS`' words; `ai` chooses the text, at most one of each every 5 s | `test_reports.AgentEvents`; real: `test_an_event_reaches_the_desktop` |
| A report is shown even when part of it fails | each step of the cleanup runs on its own | `test_reports.ExitReport.test_the_report_survives_a_failing_session`, `test_session_end.Log` |

### Projects and the agent's home

| Promise | Kept by | Tests |
|---|---|---|
| What the agent changes in its home in a project stays in that project | an overlay per project, over the base home read-only | real: `test_home_changes_stay_in_the_project` |
| The base home doesn't change under a running overlay | a shared lock for project sessions, an exclusive one without `-w` and for `--promote` | `test_cli.Concurrent`, `test_projects.Promote.test_not_while_a_session_runs` |
| Two sessions never share a work dir, nor nest; nor do projects | a lock per session, checked under a global lock; the recorded projects | `test_cli.Concurrent`, `test_cli.Overlapping` |
| `--promote` copies only what you saw, and only into the base home | a diff before asking; refuses links leading out, opaque directories, other file types | `test_projects.Promote`, `test_names.Promote`; real: `test_a_deletion_can_be_promoted`, `test_a_directory_replaced_as_a_whole_cannot_be_promoted` |
| `--reset` and `--forget` delete only `ai`'s files | `rm` and `chmod -R` follow no symlink; only the remote leading to the project | `test_projects.Reset`, `test_projects.Forget` |
| Your configuration can't be changed by someone else | refused unless yours and writable by you only | `test_cli.Config` |

## Checklist

For each change to how `ai` mounts, checks or reports, ask:

- **Name or object?** What is checked, protected or skipped by its path
  can be moved: by renaming a parent, swapping a symlink, or replacing
  it between the check and its use. Is the object itself protected?
- **What is left out?** The reports skip protected paths, `HOME_IGNORE`,
  what is hidden behind clones and private copies. Can the agent get its
  change into one of these, or make one look like another?
- **What crosses back?** What the sandbox leaves that you, your tools or
  `ai` itself will later read or run: files in the work tree, the
  clones, the agent's home, the projects' directories.
- **Which side runs it?** Any command `ai` runs on the host over what
  the agent touched (Git, find, diff, cp) must not run code from it,
  follow its links, or trust its names.
- **When?** Between a check and a mount, between an event and the
  watch on its directory, between a snapshot and the session.
- **Is there a real test?** A promise kept by the kernel needs a test
  in the real sandbox that tries to break it; the fake sandbox only shows
  what `ai` asks for.

Promises marked **no real test** above are kept only as far as the
fake sandbox can tell.
