<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../../assets/cairnml-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="../../assets/cairnml-logo.svg">
    <img src="../../assets/cairnml-logo.svg" alt="CairnML" width="480">
  </picture>
</p>

# CairnML Agent

The agent is the `cairnml[agent]` extra of the [SDK](../../README.md). It runs the jobs of a CairnML queue on this machine. It takes one job at a time from its queue, prepares the job's environment, runs it and reports the result back to the server.

## Features

- Takes jobs from one or more queues.
- Runs each job in its own Docker container. Docker is required; jobs in a Python virtual environment on the host need `--allow-venv`, which is off by default.
- Gives the agent the GPUs you choose with `--gpus`.
- Gives every job its own short-lived token, so a job never receives the agent's own token.
- Is enrolled once by a signed-in user, who signs in when the browser opens.

## Prerequisites

- Python 3.9 or newer
- A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server)
- An account on that server: any signed-in user can enrol an agent
- Docker (required; see `--allow-venv` for the exception)

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install "cairnml[agent]"
```

If the old `cairnml-agent` package is installed, uninstall it first (`pip uninstall cairnml-agent`): the old package and the extra install the same files.

## Connect to your server

```bash
cairnml-agent init http://cairnml
```

The argument is the server's address. The web app is at `<address>/cairnml` and the API at `<address>/cairnml/api`, which the command works out itself, so there are no ports to give. Your browser opens the sign-in page: sign in with your account. The browser sign-in receives its reply on `127.0.0.1:8765`, the only redirect address the identity provider accepts; if another program holds that port, the command signs in with a code instead. The configuration is stored in `~/.cairnml/agent/cairnml-agent.conf`, next to the device key; the earlier `~/cairnml-agent.conf` is still read; the file holds only the enrolment and what you set by hand (git credentials and extra package indexes); the defaults come from the installed version, so an upgrade changes them, and a file an older version wrote with a copy of the defaults is reported by `cairnml-agent doctor`.

The agent enrols as its own device, with its own key (`~/.cairnml/agent/device.key`), apart from the SDK's `cairnml init`. `--name` names the machine, `--force` enrols it again (the previous device is revoked) and `-F <file>` uses another configuration file.

## Check the machine

```bash
cairnml-agent doctor
```

It prints one line per check and the next step for each failure: the configuration names a server, the server answers, Docker is installed, the Docker daemon runs for this user, and the server has a GitHub App. A missing GitHub App is a warning: a private github.com repository then needs a credential of the project owner (Settings > Access in the web app), or the App set up by an admin. Warnings do not change the exit status (0 when no check fails, 1 otherwise). The checks change nothing. `-F <file>` checks another configuration file.

## Start the agent

```bash
cairnml-agent daemon --docker
```

Enrolling the agent (`cairnml-agent init`) made a queue for this machine and printed its name; `daemon` without `--queue` serves it. An agent serves only that queue, so send its jobs there: **Agents & Queues > Queues** lists the name.

| Option | What it does |
|---|---|
| `--queue NAME` | The queue to take jobs from. It must be a queue made for this machine when it was enrolled; the default is that queue |
| `--docker` | Run each job in a container (required unless `--allow-venv` is given) |
| `--allow-venv` | Allow jobs in a Python virtual environment on this host. Off by default: such a job can read the agent's device key |
| `--gpus 0` | The GPUs the agent may use |

Run `cairnml-agent daemon --help` for the rest.

## Trust model

The agent runs the code of every job that reaches its queue, so decide who may enqueue jobs before you start it.

- **Docker (`--docker`) is the isolated mode.** A job runs in its own container, which does not see the agent's device key (`~/.cairnml/agent/device.key`). The job gets its own token, never the agent's. Use it for any setup where jobs come from more than one person.
- **Virtual environment (`--allow-venv`) is not isolated.** A job runs as the agent's own user, so it can read the device key, whatever the file permissions. The key signs in as the user who enrolled the agent, with a token that lasts until the device is revoked. The agent prints a warning when it starts this way and still runs the job. Use it only for code you fully trust.
- The agent keeps its folder (`~/.cairnml/agent`) readable by its owner only (mode 0700), and the key in it is 0600. The agent does not put the key or its folder into a job's environment.
- A task's own Docker arguments cannot give its container host access. By default the agent drops switches such as `--privileged`, `--cap-add`, `--device`, host namespaces (`--pid`, `--ipc`, `--network`), mounts and volumes (`-v`, `--mount`), and host files (`--env-file`, `--log-opt`). The list is `forbidden_docker_args` in the agent configuration. Changing it is an administrator decision, and an empty list turns the check off.
- The agent refuses to start a job whose cache or mount folder would contain the device key (the key, its folder or a folder above them, such as the home folder). Choose cache folders outside `~/.cairnml/agent`.

## Check it

The **Workers** page of the web app lists this machine once it is running. Send a job to the queue, for example with `Task.enqueue` from the [SDK](https://github.com/gianlucapargaetzi/cairnml), and it starts on this machine.

## Configuration

The agent reads its settings from its configuration file and from environment variables. Each environment variable has one `CAIRNML_` name; [environment.md](environment.md) lists every one with its default and meaning. The older `TRAINS_` names are no longer read: rename them to the `CAIRNML_` names. The configuration file `~/trains.conf` (from the ClearML-based original) is still read, with a warning once per run; move its settings to `~/.cairnml/agent/cairnml-agent.conf`. The `K8sIntegration` option `trains_conf_file` likewise still works with a warning; use `cairnml_conf_file`.

## Next steps

Continue with the [CairnML examples](https://github.com/gianlucapargaetzi/cairnml-examples).

## Development

The agent's tests and checks are the SDK's: see [CONTRIBUTING.md](CONTRIBUTING.md) in this folder for the test commands, and the repository's [CONTRIBUTING.md](../../CONTRIBUTING.md) for the lint commands.

## Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml/issues/new) with the command you ran and the full error message.

## License

Apache License 2.0, see [LICENSE](../../LICENSE). Unofficial fork of ClearML Agent, see [DISCLAIMER.md](../../DISCLAIMER.md).
