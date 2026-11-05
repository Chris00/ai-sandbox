
# ai-sandbox

`ai` runs an AI coding agent (or any untrusted tool) in a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox on Linux.
The agent gets a working directory, a home of its own for each project,
and can reach the internet. It can't touch the rest of your files, your Git
history, your local network, your SSH keys or the services running on
your machine.

AI agents can be manipulated by text they read: a file, a web page or
an issue can carry instructions the agent then follows.  Such *prompt
injection* is the first risk of the [OWASP Top 10 for LLM Applications
2026](https://genai.owasp.org/download/56857/?tmstv=1785822482)
(LLM01:2026; see the [OWASP
page](https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/)),
and it [can't be reliably prevented in the agent
itself](https://arxiv.org/abs/2605.17634).  An additional concern is
*Excessive Agency* (LLM03:2026), which [rose from sixth to
third](https://cloudsecurityalliance.org/blog/2026/10/21/the-owasp-top-10-for-llm-applications-2026-what-changed-and-why-it-matters)
in this edition of the list.  Instead of relying on the agent
behaving, `ai` protects you in two ways:

- it makes sure your history and the rest of your system stay out of
  its reach;
- it keeps an injection from spreading to your other projects.  An
  injected instruction tries to last: written into the agent's memory,
  its instructions, its settings or a shell profile, it would act
  again in every later session, on any project.  So in each project,
  the agent's home is an overlay: what the agent writes there stays in
  that project.  At exit, `ai` reports the changes that could act
  again; you review them and choose which ones all projects get
  (`ai --promote`).

`ai` doesn't stop everything, notably sending data out (see
[Limitations](#limitations)).

```console
$ cd ~/software/project
$ ai -w                         # shell in the sandbox, at /work/software/project
$ ai -w -- claude               # run a command directly
$ ai                            # no work dir, e.g. to install tools
```

No root access is needed: everything relies on unprivileged user
namespaces.


## What it protects

| Your…                     | Inside the sandbox                                                   |
|---------------------------|----------------------------------------------------------------------|
| home directory            | replaced by `~/AI/home`, a separate home for the agent, whose changes to it in a project stay in that project (see [The agent's home](#the-agents-home)); only your TeX files (`~/texmf`) are added, read-only |
| work directory (`-w`)     | the current directory, read-write under `/work`; nothing else of yours is mounted |
| Git history and config    | each `.git` is hidden behind a clone, so the agent can't rewrite your history, hooks or config |
| build output and tool settings | `target/`, `.venv/`, `node_modules/`… replaced by private directories; settings that make tools run code (`.envrc`, `.cargo/`, `.vscode/`…) read-only (see [Tool files](#tool-files)) |
| system                    | `/usr`, `/lib`, `/bin` and TeX's generated files (`/var/lib/texmf`) read-only; a minimal `/etc`; private `/dev`, `/proc` and `/tmp` |
| environment               | cleared: no tokens or session variables leak in                      |
| network                   | internet only, through [slirp4netns](https://github.com/rootless-containers/slirp4netns); the host's localhost and the local network (printers, NAS, router…) are blocked by a firewall the agent can't change |
| SSH keys                  | never exposed; with `--ssh`, a private agent offers one dedicated key, usable only on the hosts you list |
| kernel                    | the agent can't create user namespaces, which keeps kernel features normally reserved for root out of its reach; a seccomp filter blocks rarely needed kernel interfaces with a record of exploited bugs (see [System calls](#system-calls)) |
| CPU, memory, processes    | limited with a systemd scope (half the RAM and CPUs by default)      |

The script also checks the host side, before and after each session:

- **At startup**, it asks before going on if the work directory
  contains files hard-linked from elsewhere, or other file systems
  mounted inside it.  Both would let the sandbox write outside the
  work directory. It also asks if the work directory contains Unix
  sockets or named pipes: the sandbox could talk to the program behind
  a socket, bypassing the network firewall.
- **During the session**, it watches the work directory, and as soon
  as a file or directory that makes tools on the host run code appears
  (`.envrc`, `.vscode/`, `.cargo/`, a new `.git` repo…), it shows a
  desktop notification and logs it in `~/AI/warnings.log`, with the
  time. That warns you before you use it, while you work in the project
  alongside the agent. You may also see notifications for files you
  create yourself: `ai` can't tell who created a file.
- **At exit**, it reports new symlinks pointing out of the work
  directory, and files that make tools on the host run code that the
  agent may have created or changed: `.envrc`, `.vscode/`,
  `.dir-locals.el`, `.npmrc`, `.cargo/`, `.husky/`, new `.git` repos,
  virtualenvs… Those protected at startup aren't reported, since the
  agent couldn't change them. It also reports the files of the agent's
  home that run code or steer the agent in later sessions, if the
  session changed them (see [The agent's home](#the-agents-home)).
  These warnings are also appended to `~/AI/warnings.log`.


## Requirements

- Linux ≥ 5.11 with unprivileged user namespaces (for overlayfs in
  them), systemd (`systemd-escape` names the projects' directories),
  and a systemd user session for the resource limits
- [bubblewrap](https://github.com/containers/bubblewrap) ≥ 0.10
  (`bwrap --overlay`); Debian 12 and Ubuntu 24.04 ship older versions
- `~/AI` on a file system with user extended attributes, which
  overlayfs needs (ext4, Btrfs, XFS… but not NFS)
- [slirp4netns](https://github.com/rootless-containers/slirp4netns)
- util-linux (`findmnt`)
- `nftables` (`nft`), with the `nf_tables` kernel module loaded
- GNU coreutils and findutils, Git
- Python ≥ 3.12 as `/usr/bin/python3` (`os.setns`), with libseccomp's bindings
  (`python3-seccomp` on Debian and Ubuntu, `libseccomp-python` on
  Fedora)
- for `--ssh`: OpenSSH ≥ 8.9 (destination-restricted keys)
- for the warnings during the session (optional): `inotifywait`
  (inotify-tools) and `notify-send` (libnotify). Without the first,
  new tool files are only reported at exit; without the second, they
  are only logged.

On Debian or Ubuntu:

```sh
sudo apt install bubblewrap slirp4netns util-linux nftables git python3-seccomp \
                 inotify-tools libnotify-bin
```

On a very large work directory, watching it may hit the kernel's limit
on inotify watches; `ai` then says so. Raise it with, for example,
`sudo sysctl fs.inotify.max_user_watches=1048576` (and in
`/etc/sysctl.d/` to keep it).


## Installation

1. Copy `ai` to a directory in your `PATH`, e.g. `~/.local/bin`, and
   make it executable, or link it there from your clone of this
   repository. If you copy it, also install the icons that the
   notifications use (otherwise they show a generic warning icon):

   ```sh
   install -Dm644 icons/ai-sandbox*.svg -t ~/.local/share/icons/hicolor/scalable/apps/
   ```

2. Create the agent's home and put in it only what the agent needs
   (shell configuration, the agent's own settings…):

   ```sh
   mkdir -p ~/AI/home
   ```

   Tools installed while running `ai` without `-w` go there too, and
   all projects see them (see [The agent's home](#the-agents-home)).

3. Make sure `nf_tables` is loaded at boot. It usually is already when
   ufw or firewalld is used. Otherwise:

   ```sh
   sudo modprobe nf_tables
   echo nf_tables | sudo tee /etc/modules-load.d/nf_tables.conf
   ```

4. Tell Git to ignore bare repositories it wasn't explicitly asked to
   use. Otherwise a repository planted by the agent anywhere in the work
   tree could run a command on your machine as soon as your shell prompt
   or editor runs `git status` in it:

   ```sh
   git config --global safe.bareRepository explicit
   ```

5. Tell Python on your machine to keep its bytecode cache outside your
   projects. Otherwise it would use the `.pyc` files the agent can leave
   in `__pycache__` directories next to the sources, and those can
   differ from the source you review. In your shell profile (and the
   environment of your editor):

   ```sh
   export PYTHONPYCACHEPREFIX="$HOME/.cache/pycache"
   ```

   Use an absolute path: Python doesn't expand `~` here. `ai` warns when
   the work directory contains Python files and the variable isn't set
   that way.

6. Optionally, for `--ssh`, create a key reserved for the agent, and
   register it only where the agent needs it (see [SSH](#ssh)):

   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_ai -C ai-agent
   ```

   Then set `AI_SSH_HOSTS` in `~/AI/config.py` (see
   [Configuration](#configuration)) to the hosts the agent may use it
   on, and connect once to each of them from your
   normal session so that they are in your `~/.ssh/known_hosts`.


## Usage

```
ai [-w|--work [--ro PATTERN...]...] [--ssh] [-- COMMAND [ARG...]]
ai --status
ai --promote PATH...
ai --reset | --forget [DIR] | --list
```

- `-w`, `--work`: make the current directory the work directory: it is
  mounted read-write in the sandbox, which starts there.  If the
  current directory is in your home, it is mounted at `/work/`
  followed by its path relative to your home (`~/software/project` at
  `/work/software/project`). Any other directory, meant for temporary
  experiments, is mounted at `/work-tmp/` followed by its full path
  (`/tmp/test` at `/work-tmp/tmp/test`). Each project thus has its own
  path, so tools that keep data per directory, such as an agent's
  history or memory, keep the projects apart.

  Without `-w`, the sandbox starts in the agent's home and none of
  your files are mounted, and changes to its home are seen by all
  projects.
- `--ro PATTERN...`, `--read-only PATTERN...`: make what matches the
  patterns read-only in the sandbox (see
  [Read-only files](#read-only-files)). Needs `-w`.
- `--ssh`: give the sandbox an SSH agent holding only the agent's key.
- `--status`: show what `ai` keeps for the project containing the
  current directory (see [Status](#status)).
- `--promote PATH...`: make the project's version of files of the
  agent's home the base version, for every project (see
  [Promoting a change](#promoting-a-change)).
- `--reset`, `--forget [DIR]`: delete the project's changes to the
  agent's home, or everything `ai` keeps for the project (see
  [Starting afresh](#starting-afresh)).
- `--list`: list the projects (see [Listing the projects](#listing-the-projects)).
- `-h`, `--help`: show a summary of the options and the resource limits.
- `-- COMMAND [ARG...]`: run `COMMAND` instead of an interactive shell.
  Everything after `--` is passed to it unchanged, options included:
  `ai -w -- ls -l`.

For safety, `ai` refuses as work directories `/`, your whole home
directory, and the hidden directories of your home with what they
contain. It also refuses any directory that is, contains or is inside
one of its own files (`~/AI`, the agent's SSH key, wherever your
configuration puts them) or a directory of `FORBID_WORK` (`~/Personal`
by default; see [Configuration](#configuration)). Only one session at
a time may use a given directory.

`ai` keeps what it needs for each work directory, or *project*, in a
directory of its own (see [Files](#files)).  Projects can't overlap:
once `~/software/proj` is a project, `ai` refuses to start in
`~/software/proj/sub` or in `~/software`, since a repository in both
would get two clones.  Work in the existing project, or forget it to
start afresh (see [Starting afresh](#starting-afresh)).

### Starting afresh

Two commands, run in a project's directory or below it, delete what
`ai` keeps for it, after showing it and asking:

- `ai --reset` deletes the project's changes to the agent's home (see
  [The agent's home](#the-agents-home)): its history, memory, caches,
  settings changed there… The project then sees the base home as it
  is. Promote first what you want to keep.
- `ai --forget` deletes the whole project's directory: the clones of
  its repositories, the private build directories and the changes to
  the agent's home. It also removes the `ai` remote of its
  repositories, with the `ai/…` branches fetched from it. It warns
  when the agent's branches have commits in none of yours: they would
  be lost, so fetch and merge them first, or make branches of them.
  The next session in the directory starts a new project.

Both refuse to run during a session in the project. `ai --forget DIR`
forgets the project `DIR` from anywhere, even when that directory no
longer exists, after you deleted or moved it (see
[Listing the projects](#listing-the-projects)). The clones of its
repositories may then hold the only copy of the agent's commits, so
`ai` warns about them too.

### Listing the projects

`ai --list` shows every project, with the size of what `ai` keeps for
it and the start of its latest session:

```
~/research/paper     12M  2026-10-12 09:14
~/software/old      380M  2026-06-30 17:41  gone: ai --forget ~/software/old
~/software/proj     1.3G  2026-11-01 14:02  session running
```

A project is *gone* when its directory no longer exists: deleted,
renamed or moved. A moved project gets a new, empty one at its first
session in the new place; forget the old one. `ai --list` also says
when `~/AI/work` or `~/AI/shadow`, from an older version of `ai`, still
hold projects to migrate (see [Files](#files)).

### Status

`ai --status`, in a project's directory or any directory below it,
shows what `ai` keeps for it:

```
Project: ~/software/proj
Kept in: ~/AI/projects/home-you-software-proj (1.3G)
Session: none
Repo .:
  ai/fix-parser: 3 commits not in your branches
  ai/main: new commits, not fetched yet (git fetch ai)
Repo vendor/lib: nothing left to merge
Home layer: 2.4M, changes to the agent's home:
  .claude/.credentials.json
! .claude/skills/review/ (3 files)
! .bashrc
  and 37 more in HOME_IGNORE (histories, caches...).
!: can run code, or steer the agent, in its later sessions.
Private copies:
  .venv: 85M
  target: 1.2G
```

For each repository, it lists the agent's branches with commits not in
your branches (see [Working with Git](#working-with-git)). It then
lists the project's changes to the agent's home, summed up as in the
report at exit (see [The agent's home](#the-agents-home)), and each
private build directory (see [Tool files](#tool-files)) with its size,
noting those the project no longer uses.  It reads the clones from
your repositories, like `git fetch ai`, and runs nothing in them.


## The agent's home

`~/AI/home` is the agent's base home: its shell configuration, its
tools, its settings and login. A session without `-w` works in it
directly, to install or update tools for all projects.

In a project, the agent sees an overlay instead: the base home,
read-only, with the project's own changes on top, kept in the
project's `home/` (see [Files](#files)). Whatever the agent writes to
its home there (history, memory, settings, caches, a modified shell
profile…) stays in the project: other projects don't see it, and a
manipulated agent can't use it to reach them. `ai --status` lists
these changes.

A change to the base home is seen in every project, unless the project
has changed that file itself: its own copy then wins. Since the base
home must not change under a running overlay, `ai` refuses to start a
session without `-w` while project sessions run, and project sessions
while it runs.

For the same reason, the automatic updates of Claude Code and GitHub
Copilot CLI are turned off in projects (`DISABLE_AUTOUPDATER=1` and
`COPILOT_AUTO_UPDATE=false`, in `AI_PROJECT_ENV`, see
[Configuration](#configuration)): an update there would copy the whole
new version into that project only. Update in a session without `-w`.

The base home's files are the same in every session, so they can't
tell a project session from one without `-w` by themselves. For that,
`ai` sets `AI_PROJECT`, in project sessions only, to the work
directory's path in the sandbox (`/work/software/proj`). A shell
profile can then act in projects only, for settings that an agent only
reads from its configuration files. Codex, for one, has no environment
variable to turn off its update prompt, but takes the setting on its
command line:

```bash
# ~/AI/home/.bashrc
if [ -n "$AI_PROJECT" ]; then
    alias codex='codex -c check_for_update_on_startup=false'
fi
```

An alias only applies in the interactive shell, not to `ai -w --
codex`. For that, put a wrapper script named `codex` in a directory of
the base home placed first in the `PATH` (set in `AI_ENV`).

Likewise, log the agent in from a session without `-w`, so that all
projects see the credentials. But a login refreshes its credentials
from time to time, and a refresh during a project session stays in
that project, which may then need its own login. A long-lived token
avoids this, since nothing is written back: for Claude Code, generate
one with `claude setup-token` and give it to every session in
`AI_ENV`.

At exit, `ai` sums up the changes the session made to the agent's
home, and marks with `!` those that make code run, or steer the agent,
in its later sessions: shell profiles, `~/.local/bin`, Git's and SSH's
configuration, package managers' settings, Claude Code's settings
(hooks, permissions), its instructions (`CLAUDE.md`, the projects'
memory) and its program. That list, `HOME_TRIGGERS`, can be extended
in your [configuration](#configuration). For example:

```
2026-11-01 14:31:30 Session in /home/you/software/proj:
Changed in the agent's home, in this project only (in ~/AI/projects/home-you-software-proj/home):
  .claude/.credentials.json
! .claude/settings.json (deleted)
! .claude/skills/review/ (3 files)
! .claude/projects/-work-software-proj/memory/MEMORY.md
! .bashrc
  .config/gh/ (2 files)
  and 12 more in HOME_IGNORE (histories, caches...).
!: can run code, or steer the agent, in its later sessions.
Once reviewed, ai --promote PATH in the project makes one the base version.
```

The summary is shown on the terminal. Only the entries marked `!` go
to `~/AI/warnings.log`, as warnings. `ai --status` gives the same
summary of all the project's changes to the agent's home.

In a project, the change is in its `home/`: review it there, and
delete the file to go back to the base version, or promote it (see
below). After a session without `-w`, the report covers only the
files marked `!`, in the base home, which every project sees.

The summary first shows what matches `HOME_GROUPS`, in its order, then
the rest, two levels down (`.config/gh/`). A directory is shown as one
entry, with the number of files changed in it, unless there is only
one. By default, the list puts first your login (`.credentials.json`,
which you may want to promote) and Claude Code's settings, then shows
each skill, plugin, marketplace or project's memory as one entry,
since what an installation puts there belongs together. A file goes
with the first pattern that matches it or one of its directories, so
`.claude/skills/synced/*`, listed before `.claude/skills/*`, shows the
skills synced from claude.ai one by one. `HOME_GROUPS` can be changed
in your configuration as well.

Files that change in about every session are only counted: shell and
other histories, `~/.cache`, npm's cache, Claude Code's state,
transcripts, backups and to-do lists… Their list, `HOME_IGNORE`, can be
extended in your configuration too. Claude Code's `~/.claude.json` is
among them, although it holds its MCP servers: Claude Code rewrites it
at every start, so it would always show. A file in both lists is
marked `!`.

### Promoting a change

A change made in a project, such as a skill the agent wrote or a
setting you adjusted there, may be worth having in every project.
`ai --promote` copies it to the base home, in the project's directory:

```sh
cd ~/software/proj
ai --promote ~/.claude/skills/review ~/.bashrc
```

Each path is a file of the agent's home, written `~/…` or relative to
it. It may be a file, a symlink, a deletion (the base's file is then
deleted), or a directory, for all the changes in it. `ai` first shows
each change, as a diff against the base version, and asks before
copying. The promoted files then leave the project's layer, so the
project sees the base version, now the same, and follows it again.

A promoted file runs, or steers the agent, in every project: review it
as code from an unknown source. Promoting needs that no session runs,
since the base home must not change under one. `ai` refuses a symlink
of the base home that would lead the copy out of it, and a directory
that the project deleted and created again, which replaces the base's
as a whole: copy what you need from it by hand.

*Remark.* A skill is a directory: Claude Code finds the skills in
`~/.claude/skills/` at startup, so promoting the directory is
enough. A plugin isn't: Claude Code keeps its files in
`~/.claude/plugins/cache/`, records the install in
`~/.claude/plugins/installed_plugins.json` (and its marketplace in
`known_marketplaces.json` and `marketplaces/`), and enables it in
`~/.claude/settings.json`. Promoting all of these would also bring the
project's other changes to the two JSON files, which hold every
plugin.  Rather install plugins in a session without `-w`, for all
projects.


## Notifications from the agent

An agent can tell you, with a desktop notification, that it has
finished or needs you, so you don't have to check its terminal. It
reports it from one of its hooks, by writing a word such as `stop` or
`permission_prompt` to the pipe `$AI_NOTIFY`; `ai`, outside the
sandbox, shows the notification. Only a few known words are accepted,
and the text is `ai`'s own: the agent can choose the event, never what
the notification says, and nothing gives it access to your desktop.
Each kind of notification is shown at most once every 5 seconds.

The hooks go in the base home, for all projects. See
[docs/notifications.md](docs/notifications.md) for Claude Code, Codex,
GitHub Copilot CLI and Kimi CLI.


## Read-only files

`--ro` protects parts of the work directory that the agent should read
but not change, such as reference documents, raw data or a whole
project to review:

```sh
cd ~/research/paper
ai -w --ro '*.pdf' data/raw -- claude
ai -w --ro .                           # everything read-only
```

`--ro` takes all the patterns up to the next option or `--`, and can
be repeated.

| Pattern                 | Matches                                                    |
|-------------------------|------------------------------------------------------------|
| without `/` (`*.pdf`)   | names anywhere in the work directory                       |
| with `/` (`docs/*.pdf`) | paths relative to the work directory; `*` also matches `/` |
| `.`                     | the whole work directory                                   |
| an absolute path        | the same, if it is in the work directory                   |

Quoting a pattern makes it reach subdirectories: `--ro '*.pdf'`
protects every PDF in the work directory. Unquoted, your shell expands
`*.pdf` itself, to the PDFs of the current directory only (bash leaves
it as is when there are none, and zsh refuses to run the command). A
matching directory is read-only with everything in it. A pattern
starting with `-` needs the `--ro=PATTERN` form, or `./` in front.

Only what exists at startup is protected: a new file matching a pattern
that the agent creates during the session is writable. Each match is a
separate mount, so a pattern on a directory is better than one matching
thousands of files. `.git`, the private directories (`target/`,
`.venv/`…) and symlinks are skipped: the agent keeps its own clone and
build directories, even with `--ro .`, so it can still commit and
build, but not change your files.


## Working with Git

For every repository in the work directory, the sandbox sees a clone
kept in the project's directory, under `git/` (see [Files](#files)),
instead of your real `.git`. The working tree is shared, but the history isn't. Your real
repository is visible read-only as the clone's `origin`, so
`git fetch origin` inside the sandbox picks up your latest commits.

The first time `ai` meets a repository, it asks whether you created it.
Answer no if the agent may have created it in an earlier session.

To bring the agent's commits back into your repository:

```sh
git fetch ai            # the remote is added on first use
git log -p HEAD..ai/main
git diff ai/main        # empty when the files match
git reset ai/main       # move your branch, keep the files
```

`git reset` rather than `git merge`: the working tree already holds the
agent's changes, so a merge would complain about them.

**From the host, only ever fetch from the clones.** Never push to them
and never run Git commands inside them: their configuration and hooks
are controlled by the agent. Fetching is safe, because it runs no code
from the clone. The `ai` remote is configured to skip tags and to
verify the objects it receives.

Not handled: submodules and worktrees, whose `.git` is a file rather
than a directory. `ai` lists them at startup.


## Tool files

Some files in a project make tools on your machine run code: when you
build it, open it in an editor, or simply enter its directory. Since
you may work in the project while the agent does, `ai` keeps the
agent's hands off them for the whole session, rather than only
reporting changes at the end. Two lists, which your
[configuration](#configuration) can change, set which ones:

- **`PRIVATE`** (build output and environments: `target/`, `.venv/`,
  `venv/`, `node_modules/`, `.direnv/`): the sandbox sees a private
  directory instead, kept in the project's directory, under `shadow/`
  (see [Files](#files)). The agent builds and
  installs there, and keeps its builds between sessions; yours stay
  untouched. The agent starts with an empty one, so it rebuilds or
  reinstalls once.
- **`READONLY`** (settings: `.envrc`, `.cargo/`, `.vscode/`, `.idea/`,
  `.dir-locals.el`, `.npmrc`, `mise.toml`, `.husky/`,
  `.pre-commit-config.yaml`, `.gitattributes`, `.gitmodules`…): the
  agent sees yours, read-only, so it builds and commits as you do but
  can't change them. If a change is needed, it can propose it for you
  to apply.

`ai` lists them at startup. Only entries that already exist can be
protected, anywhere in the work directory, except `target/`, `.venv/`,
`node_modules/` and `.direnv/` at the top, which are protected as soon
as `Cargo.toml`, `pyproject.toml`, `package.json` or `.envrc` is there:
the empty directory created for them in your tree is removed at exit.
Entries the agent creates elsewhere during the session can't be
protected: they are reported as they appear, and again at exit.
Symlinks are skipped.

Inside the sandbox, Python keeps its bytecode in the agent's home
(`PYTHONPYCACHEPREFIX`) rather than in `__pycache__` directories of
your tree. That doesn't stop the agent from writing some there on
purpose: hence the setting for your own machine in
[Installation](#installation).


## SSH

`--ssh` starts a private `ssh-agent` for the session and loads only
`~/.ssh/id_ed25519_ai` in it. Your own agent and keys stay out of the
sandbox. The agent can use the key while the session runs, but can't
copy it, and the agent stops with the session. Your `known_hosts` is
available read-only, as the sandbox's system-wide list of known hosts
(`/etc/ssh/ssh_known_hosts`), so that it leaves nothing in the agent's
home.

The agent only uses the key for the hosts listed in `AI_SSH_HOSTS`
(`github.com` and `gitlab.com` by default), which you can set in your
[configuration](#configuration):

```bash
AI_SSH_HOSTS=(github.com gitlab.com git@codeberg.org)
```

Each entry is a host name, optionally with a user (`git@codeberg.org`).
The agent recognizes a host by its key, so every listed host must be in
your `~/.ssh/known_hosts`, or `ssh-add` reports an error. An
empty list lets the key be used on any host that accepts it.

The key still only limits the risk if servers limit what it may do:

- **GitHub or GitLab:** use a separate account that is a collaborator
  only on the repositories the agent works on, or a deploy key per
  repository.
- **Your own servers:** add `restrict` (or a fixed `command=`) to the
  key's line in `authorized_keys`.


## Network

The sandbox has its own network namespace, connected to the internet by
slirp4netns. A firewall inside that namespace rejects connections to:

- private and link-local ranges (`10/8`, `172.16/12`, `192.168/16`,
  `100.64/10`, `169.254/16`, `fc00::/7`, `fe80::/10`), which cover home
  and office networks and most VPNs
- multicast (printer and TV discovery)
- the host itself (`10.0.2.2`, and `--disable-host-loopback`)

The only exception is slirp4netns's DNS forwarder, `10.0.2.3:53`. The
rules are installed before the sandbox starts, as root of a user
namespace the sandbox isn't in, so the agent can't change them.

To check, from inside the sandbox:

```sh
curl -m 3 http://192.168.1.1        # fails at once: "Connection refused"
curl -sI https://example.com        # works
```

`ping` doesn't work in the sandbox: ICMP needs privileges it doesn't
have.


## System calls

Everything in the sandbox runs under a seccomp filter, built at each
launch. It blocks kernel interfaces that ordinary programs can do
without, and that have a long record of exploited bugs:

| Blocked                                       | Error          |
|-----------------------------------------------|----------------|
| `io_uring` (programs fall back to classic I/O) | `ENOSYS`      |
| `bpf`, `userfaultfd`, `perf_event_open`       | `EPERM`        |
| kernel keyrings (`keyctl`, `add_key`, `request_key`) | `EPERM` |
| mounting (refused anyway without privileges)  | `EPERM`        |
| typing into the terminal (`TIOCSTI`, `TIOCLINUX`) | `EPERM`    |
| socket families other than Unix, IPv4, IPv6 and netlink | `EAFNOSUPPORT` |

32-bit system calls (i386 and x32) kill the process, so that they can't
be used to get around the filter.

As a result, profilers based on `perf_event_open` and 32-bit programs
don't work in the sandbox. If a tool needs one of the blocked calls,
remove it from the list in the script.

To check that the filter is active, from inside the sandbox:

```sh
grep Seccomp /proc/self/status    # "Seccomp: 2"
python3 -c 'import socket; socket.socket(socket.AF_PACKET, socket.SOCK_RAW)'
# OSError: [Errno 97] Address family not supported by protocol
```


## Resource limits

`ai` restarts itself in a systemd user scope, so the whole session (the
sandbox, slirp4netns and ssh-agent) shares these limits:

| Variable    | Default         | Limit                                     |
|-------------|-----------------|-------------------------------------------|
| `AI_MEMORY` | `50%`           | RAM, page cache included                  |
| `AI_SWAP`   | half your swap  | swap (`0` to forbid it)                   |
| `AI_CPUS`   | half your CPUs  | number of CPUs' worth of time             |
| `AI_TASKS`  | `1024`          | processes and threads                     |

Override them from the environment:

```sh
AI_CPUS=2 AI_MEMORY=8G ai -w
```

The session also runs at a lower CPU and I/O priority than your other
programs. When it runs out of memory, the kernel kills a process inside
the sandbox, never one of your other programs, and the rest of the
session goes on.

CPU and I/O limits need the `cpu` and `io` controllers delegated to
your user session. Check with:

```sh
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
```

If they're missing:

```sh
sudo mkdir -p /etc/systemd/system/user@.service.d
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' |
    sudo tee /etc/systemd/system/user@.service.d/delegate.conf
sudo systemctl daemon-reload    # then log out and back in
```

Without a systemd user session, `ai` runs without limits and says so.


## Configuration

`ai` reads `~/AI/config.py`, if it exists: Python code, run after the
defaults are set (in `default_settings`, at the top of the script), with
these settings as variables, and `HOME`. Since it runs with your rights,
it must be yours and writable by you only. Make it readable by you only
too if it holds secrets:

```python
# ~/AI/config.py (chmod 600)

# Environment variables, as NAME=value: AI_ENV for every session,
# AI_PROJECT_ENV for project sessions (-w) only.
AI_ENV = [
    "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…",    # from claude setup-token
]
AI_PROJECT_ENV += [
    # Add the equivalent of DISABLE_AUTOUPDATER for another agent.
]

HOME_TRIGGERS += [".config/myagent"]     # see The agent's home
HOME_IGNORE += [".local/state/myagent"]
READONLY += [".myrc"]                    # see Tool files
PRIVATE += ["_build"]
PRIVATE_IF["_build"] = "dune-project"
HOME_GROUPS += [".config/myagent/*"]
AI_SSH_HOSTS = ["github.com"]            # replaces the default list
FORBID_WORK += [f"{HOME}/Documents/taxes"]
```

Use `+=` to add to a list: `=` replaces it, defaults included. The other
settings of `default_settings` can be changed the same way. A setting of
the wrong type, or an error in the file, stops `ai` with the line
concerned.
The sandbox's environment is cleared, except for a few variables set by
`ai` (`HOME`, `PATH`, `TERM`…); the variables of `AI_ENV` and
`AI_PROJECT_ENV` come after them, so they can replace them. `ai` passes
them to bwrap through a file in memory rather than on its command line,
which every local user can read with `ps`.

The resource limits (see [Resource limits](#resource-limits)) can be
set there too. Write them `AI_CPUS = AI_CPUS or 4`, so that the
environment still takes precedence (`AI_CPUS=2 ai -w`).

An older `~/AI/config`, in bash, is no longer read: `ai` refuses to
start while it is there without a `~/AI/config.py`, as going on would
drop what it asks. Write its settings in Python, then delete it.


## Files

| Path                 | Contents                                         |
|----------------------|--------------------------------------------------|
| `~/AI/home`          | the agent's home directory                       |
| `~/AI/projects/`     | one directory per project (see below)            |
| `~/AI/config.py`     | your configuration, optional (see [Configuration](#configuration)) |
| `~/AI/locks`         | the locks of the running sessions                |
| `~/AI/warnings.log`  | warnings from the checks during the session and at exit, with their time |
| `~/.ssh/id_ed25519_ai` | the agent's SSH key (`--ssh`)                  |

The paths are set at the top of the script, and can be changed in
`~/AI/config.py`.

A project's directory is named after the project's path, escaped by
`systemd-escape` so that two projects never nest (`~/software/proj`
gives `home-you-software-proj`).  A name that would be too long for a
file name keeps its first 183 characters, followed by a hash of the
whole path.  It holds:

| Path      | Contents                                                       |
|-----------|----------------------------------------------------------------|
| `path`    | the project's path                                             |
| `git/`    | the clones of the project's repositories, at their path in it  |
| `shadow/` | the agent's private build output and environments              |
| `home/`   | the project's changes to the agent's home (the overlay's upper layer) |
| `home.work/` | overlayfs's work directory                                  |

`ai --forget` deletes a project's directory (see
[Starting afresh](#starting-afresh)). To do it by hand, make it
writable first: overlayfs leaves an unreadable directory in
`home.work/`:

```sh
chmod -R u+rwx ~/AI/projects/home-you-software-proj
rm -rf ~/AI/projects/home-you-software-proj
```

Older versions of `ai` kept the clones in `~/AI/work` and the private
directories in `~/AI/shadow`.  The first session in a project moves
them to its directory, and updates the `ai` remote of its repositories.
`ai --list` says when some remain.

A few directories of the host are also available in the sandbox,
read-only, so that the agent can compile your LaTeX documents:

| Path             | Contents                                                |
|------------------|---------------------------------------------------------|
| `~/texmf`        | your own TeX files: classes, packages, fonts…           |
| `/var/lib/texmf` | the system's generated TeX files (formats, font maps…)  |

Both are optional: `ai` skips them if they don't exist. `~/texmf` is
TeX Live's default personal tree, searched without any setting, so it
works in the sandbox even though the environment is cleared. Its files
must follow the TeX Directory Structure: classes and packages anywhere
under `~/texmf/tex/` (e.g. `~/texmf/tex/latex/mypackage/`), fonts under
`~/texmf/fonts/`. To check that a file is found, run `kpsewhich
myclass.cls` in the sandbox.

`~/AI/home` and `~/AI/projects` are controlled by the agent. On the host,
treat them like a download from an unknown source: review files before
opening them in an editor, entering the directories with a Git-aware
prompt or running commands there.

## Limitations

The sandbox narrows what an agent can do, but it doesn't make
untrusted code harmless.

- **Exfiltration.** Whatever the sandbox can read can be sent to the
  internet: the work directory, `~/AI/home` (including the agent's own
  API key), your TeX files in `~/texmf` and your real `.git` under
  `/upstream` (check that your remote URLs in `.git/config` contain no
  tokens).
- **Your online accounts.** With a claude.ai login, Claude Code also
  gets the connectors of your claude.ai account (Gmail, Google Drive,
  Slack…, set at claude.ai/customize/connectors), which reach your data
  online, out of the sandbox's control. A token from `claude
  setup-token` or an API key in `AI_ENV` doesn't load them (see
  [Configuration](#configuration)).
- **Code you run later.** The agent's changes land in your working
  tree. Review them before running the code, its build files or its CI
  configuration on the host.  In particular, `ai` assumes agents only
  run in the sandbox: the files that configure or instruct them in a
  project (`.claude/`, `CLAUDE.md`, `AGENTS.md`,
  `.github/copilot-instructions.md`…) aren't checked, so an agent run
  on that project outside the sandbox, *including an assistant in your
  editor*, would follow what the sandboxed agent wrote there.
- **The agent's own directories.** `~/AI/home` and `~/AI/projects` are
  controlled by the agent, and the exit check doesn't scan them. On the
  host, treat them like the work tree (see [Files](#files)).
- **The kernel.** All sandboxing relies on the Linux kernel, and a
  kernel bug can break it. The seccomp filter makes much of the kernel
  unreachable, but not all of it. Keep your system updated.
- **Terminal escape sequences.** The sandbox writes to your real
  terminal, and no filter can tell harmless output from escape
  sequences. Some terminals let a program set your clipboard (OSC 52),
  so a paste could contain something you didn't copy. Terminals based
  on VTE (GNOME Terminal, Ptyxis) ignore OSC 52; in others, disable
  clipboard writes in the settings if possible. The names `ai` itself shows,
  such as paths in the work directory or branch names, may come from
  the agent: their control characters are replaced by `?`.
- **Disk space.** It isn't limited. `~/AI` and the work directory share
  your disk. Put `~/AI` on a separate file system, or use quotas, if
  that matters.
- **Public addresses.** Only private network ranges are blocked. Servers
  on public addresses, such as some company networks, remain reachable.
- **Persistence between sessions.** What the agent leaves in its home
  in a project (in its shell profile, its tools' configuration or
  installed packages) runs in that project's later sessions. What it
  leaves there in a session without `-w` runs in every project: keep
  those sessions for installing and updating trusted tools.
