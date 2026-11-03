# Notifications from the agent

`ai` shows a desktop notification when the agent in a session reports
that it has finished, or needs you. The agent reports it from one of
its *hooks*, commands it runs at those moments, by writing one word to
the pipe `$AI_NOTIFY`:

| Word                 | Notification                        |
|----------------------|-------------------------------------|
| `stop`               | The agent has finished.             |
| `permission_prompt`  | The agent asks for your permission. |
| `idle_prompt`        | The agent is waiting for you.       |
| `elicitation_dialog` | The agent has a question.           |
| `input`              | The agent needs your attention.     |

The title names the project. `ai` ignores any other line, and shows each
kind of notification at most once every 5 seconds: the agent chooses
which event to report, never what the notification says. The list is
`AGENT_EVENTS`, at the top of the script; your
[configuration](../README.md#configuration) can change it.

Put the hooks in the base home (`~/AI/home`), so that every project
uses them: edit its files from your machine while no session runs, or
in a session without `-w`. They are among the files the exit report
watches (`HOME_TRIGGERS`), since hooks run code in every later session.
Writing to `${AI_NOTIFY:-/dev/null}` makes a hook harmless outside `ai`.

The examples below follow each agent's documentation. Check them with
your version: the hooks of Codex, Copilot CLI and Kimi CLI in
particular change quickly.


## Claude Code

In `~/AI/home/.claude/settings.json` (merge the `hooks` key with what
the file already holds):

```json
{
  "hooks": {
    "Notification": [
      {
        "matcher": "permission_prompt",
        "hooks": [{"type": "command",
                   "command": "echo permission_prompt >\"${AI_NOTIFY:-/dev/null}\""}]
      },
      {
        "matcher": "elicitation_dialog",
        "hooks": [{"type": "command",
                   "command": "echo elicitation_dialog >\"${AI_NOTIFY:-/dev/null}\""}]
      }
    ],
    "Stop": [
      {
        "hooks": [{"type": "command",
                   "command": "echo stop >\"${AI_NOTIFY:-/dev/null}\""}]
      }
    ]
  }
}
```

`Stop` runs at the end of each of Claude's turns. Claude Code also
reports `idle_prompt` when it has waited for you for a while after a
turn: add it like the others if you want a reminder.


## Codex CLI

In `~/AI/home/.codex/config.toml`, `notify` runs a command at the end of
each turn (it receives a description of the turn as its argument, which
the command below ignores):

```toml
notify = ["sh", "-c", "echo stop >\"${AI_NOTIFY:-/dev/null}\""]
```


## GitHub Copilot CLI

In a file of `~/AI/home/.copilot/hooks/`, say `ai.json`:

```json
{
  "version": 1,
  "hooks": {
    "agentStop": [
      {"type": "command", "bash": "echo stop >\"${AI_NOTIFY:-/dev/null}\""}
    ],
    "notification": [
      {"type": "command", "bash": "echo input >\"${AI_NOTIFY:-/dev/null}\""}
    ]
  }
}
```

`notification` runs for all the CLI's notifications (permission
requests, questions, the agent being idle), hence the general `input`.


## Kimi CLI

In `~/AI/home/.kimi/config.toml`:

```toml
[[hooks]]
event = "Notification"
matcher = "permission_prompt"
command = 'echo permission_prompt >"${AI_NOTIFY:-/dev/null}"'

[[hooks]]
event = "Stop"
command = 'echo stop >"${AI_NOTIFY:-/dev/null}"'
```


## Other agents

Any agent that can run a command when it finishes or needs you can do
the same: write one of the words above, followed by a newline, to
`$AI_NOTIFY`.
