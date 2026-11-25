# 0002. A job's repository is read with the server's token or none, never with the host's credentials

## Status

Accepted (2026-11-25). Tracked in cairnml#194. Reverses the decision of cairnml#182 (pass the agent's SSH agent socket
into job containers instead of warning).

## Context

The agent was inherited with several ways to read a repository with credentials that belong to the machine, not to the
job: it mounted the host's SSH agent socket, or a copy of `~/.ssh`, into every job container, mounted `~/.git-credentials`
and `~/.gitconfig`, cloned with the host's own `ssh` and git configuration, and fell back to credentials from its
configuration (`agent.git_user`, `agent.git_pass`, extra credentials) or its environment. The code of any task that
reaches the agent, including another user's task, could therefore act with the agent owner's SSH keys and stored git
credentials. The server already issues a short-lived, repository-scoped token for each job (the GitHub App installation
token, or a credential stored for another host), so the host's credentials were never needed.

## Decision

- A job container receives nothing from the host's SSH or git setup: no SSH agent socket or `SSH_AUTH_SOCK`, no copy of
  `~/.ssh`, no `~/.git-credentials`, no `~/.gitconfig`.
- The only credential a repository is read with is the token the server issues for that repository. An answer without a
  token means the repository is public or has no credential, and the clone is anonymous; no answer or a refusal fails the
  job. A private repository that cannot be cloned without a credential fails the job with a message that names the
  repository and says to assign a credential or install the GitHub App.
- The agent's git commands run in an isolated environment: no SSH agent, no `ssh` (`GIT_SSH_COMMAND` refuses), no askpass
  program of the host, and neither the host's nor the system's git configuration (the agent's own global file stands in).
  The agent's values win over the process environment.
- The token is sent only to the origin of the clone address (`http.<origin>/.extraheader`), so a submodule on another host
  never receives it.
- An address in ssh form is cloned over https for GitHub, and refused for any other host with a message that asks for
  the https address. The agent never calls `ssh` for a job.
- The settings and environment variables that supplied credentials or SSH behaviour are removed and ignored, with one
  warning at start.
- One credential stays: a password written in the task's own repository address. It belongs to the task, not to the
  host or the agent; it is the task owner's explicit choice (owner decision, 2026-11-25).

## Alternatives considered

- **Keep the socket mount and warn (cairnml#182).** Rejected: a warning does not stop job code from using the keys.
- **A setting that lets an administrator allow the host's SSH setup.** Rejected: it re-opens the hole and keeps the
  inherited code; extra docker arguments that an administrator configures remain the administrator's responsibility.
- **Probe the repository on the host before starting the container, to fail "before the container starts".** Rejected
  (owner decision): the agent does not probe; the clone is the job's first step and fails within seconds with the same
  message. A probe would need its own isolated git call on the host.
- **Ignore a password in the task's own address too.** Rejected (owner decision): the credential is the task's own.

## Consequences

- A host that reaches a repository only through an ssh key must store a credential on the server and use the https
  address; there is no agent-side substitute.
- In virtual-environment mode the job's code runs as the agent's own user and can read that user's `~/.ssh` and use an
  SSH agent directly; the agent cannot prevent that. Docker mode is the isolated mode.
- A cached repository made by an earlier agent may hold credentials in its origin address; the origin is reset to this
  run's address before every pull.
- The vcs cache folder is shared by the tasks of one agent; sharing a private repository's files between tasks is
  unchanged.
