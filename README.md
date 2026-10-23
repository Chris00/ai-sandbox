
# ai-sandbox

`ai` runs an AI coding agent (or any untrusted tool) in a
[bubblewrap](https://github.com/containers/bubblewrap) sandbox on Linux.
The agent gets a working directory and a home of its own, and can reach
the internet. It can't touch the rest of your files, your Git
history, your local network, your SSH keys or the services running on
your machine.

```console
$ ai -w ~/software/project      # shell in the sandbox, project at /work
$ ai -w ~/research/paper claude # run a command directly
$ ai                            # no work dir, e.g. to install tools
```

No root access is needed: everything relies on unprivileged user
namespaces.


## What it protects

| Your…                     | Inside the sandbox                                                   |
|---------------------------|----------------------------------------------------------------------|
| home directory            | replaced by `~/AI/home`, a separate home for the agent               |
| work directory (`-w DIR`) | read-write at `/work`; nothing else of yours is mounted              |
| Git history and config    | each `.git` is hidden behind a clone, so the agent can't rewrite your history, hooks or config |
| system                    | `/usr`, `/lib` and `/bin` read-only; a minimal `/etc`; private `/dev`, `/proc` and `/tmp` |
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
- **At exit**, it reports new symlinks pointing out of the work
  directory, and new or changed files that make tools on the host run
  code: `.envrc`, `.vscode/`, `.dir-locals.el`, `.npmrc`, `.cargo/`,
  `.husky/`, new `.git` repos, virtualenvs… These warnings are also
  appended to `~/AI/warnings.log`.


## Requirements

- Linux with unprivileged user namespaces, and a systemd user session
  for the resource limits
- [bubblewrap](https://github.com/containers/bubblewrap) ≥ 0.8
  (`bwrap --disable-userns`)
- [slirp4netns](https://github.com/rootless-containers/slirp4netns)
- util-linux ≥ 2.39 (`nsenter --user-parent`, `lsns`, `findmnt`, `flock`)
- `nftables` (`nft`), with the `nf_tables` kernel module loaded
- GNU coreutils and findutils, `awk`, Git
- Python 3 with libseccomp's bindings (`python3-seccomp` on Debian
  and Ubuntu, `python3-libseccomp` on Fedora)
- for `--ssh`: OpenSSH ≥ 8.9 (destination-restricted keys)

On Debian or Ubuntu:

```sh
sudo apt install bubblewrap slirp4netns util-linux nftables git python3-seccomp
```


## Installation

1. Copy `ai` to a directory in your `PATH`, e.g. `~/.local/bin`, and
   make it executable.

2. Create the agent's home and put in it only what the agent needs
   (shell configuration, the agent's own settings…):

   ```sh
   mkdir -p ~/AI/home
   ```

   Tools installed while running `ai` without `-w` go there too.

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

5. Optionally, for `--ssh`, create a key reserved for the agent, and
   register it only where the agent needs it (see [SSH](#ssh)):

   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_ai -C ai-agent
   ```

   Then set `AI_SSH_HOSTS` at the top of the script to the hosts the
   agent may use it on, and connect once to each of them from your
   normal session so that they are in your `~/.ssh/known_hosts`.


## Usage

```
ai [--ssh] [-w|--work DIR] [--] [COMMAND [ARG...]]
```

- `-w DIR`, `--work DIR`: mount `DIR` read-write at `/work` and start
  there. Without it, the sandbox starts in the agent's home.
- `--ssh`: give the sandbox an SSH agent holding only the agent's key.
- `COMMAND`: run this instead of an interactive shell. Everything after
  the first non-option argument is passed to it unchanged, so
  `ai -w proj ls -l` works.

For safety, `ai` refuses `/`, your whole home directory, hidden
directories in your home, `~/AI` itself and `~/Personal` as work
directories. Only one session at a time may use a given directory, or
a directory containing or inside it.


## Working with Git

For every repository in the work directory, the sandbox sees a clone
kept in `~/AI/work/<path relative to your home>` instead of your real
`.git`. The working tree is shared, but the history isn't. Your real
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
than a directory. `ai` lists them at startup. Repositories in a work
directory outside your home aren't protected either, and `ai` warns
about it.


## SSH

`--ssh` starts a private `ssh-agent` for the session and loads only
`~/.ssh/id_ed25519_ai` in it. Your own agent and keys stay out of the
sandbox. The agent can use the key while the session runs, but can't
copy it, and the agent stops with the session. Your `known_hosts` is
available read-only.

The agent only uses the key for the hosts listed in `AI_SSH_HOSTS`, at
the top of the script (`github.com` and `gitlab.com` by default):

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
AI_CPUS=2 AI_MEMORY=8G ai -w ~/software/project
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


## Files

| Path                 | Contents                                         |
|----------------------|--------------------------------------------------|
| `~/AI/home`          | the agent's home directory                       |
| `~/AI/work`          | the clones of your repositories' `.git`          |
| `~/AI/locks`         | one lock per running session                     |
| `~/AI/warnings.log`  | warnings from the checks at exit                 |
| `~/.ssh/id_ed25519_ai` | the agent's SSH key (`--ssh`)                  |

The paths are set at the top of the script.

Everything under `~/AI` is controlled by the agent. On the host, treat
it like a download from an unknown source: review files before opening
them in an editor, entering the directories with a Git-aware prompt or
running commands there.


## Limitations

The sandbox narrows what an agent can do, but it doesn't make
untrusted code harmless.

- **Exfiltration.** Whatever the sandbox can read can be sent to the
  internet: the work directory, `~/AI/home` (including the agent's own
  API key) and your real `.git` (check that your remote URLs contain no
  tokens).
- **Code you run later.** The agent's changes land in your working
  tree. Review them before running the code, its build files or its CI
  configuration on the host.
- **The kernel.** All sandboxing relies on the Linux kernel, and a
  kernel bug can break it. The seccomp filter makes much of the kernel
  unreachable, but not all of it. Keep your system updated.
- **Disk space.** It isn't limited. `~/AI` and the work directory share
  your disk. Put `~/AI` on a separate file system, or use quotas, if
  that matters.
- **Public addresses.** Only private network ranges are blocked. Servers
  on public addresses, such as some company networks, remain reachable.
