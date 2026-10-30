<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/cairnml-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/cairnml-logo.svg">
    <img src="assets/cairnml-logo.svg" alt="CairnML" width="480">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/gianlucapargaetzi/cairnml-agent/actions/workflows/ci.yml"><img src="https://github.com/gianlucapargaetzi/cairnml-agent/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/cairnml-agent/"><img src="https://img.shields.io/pypi/v/cairnml-agent?style=flat-square&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://pypi.org/project/cairnml-agent/"><img src="https://img.shields.io/pypi/dm/cairnml-agent?style=flat-square&label=monthly%20downloads" alt="Monthly downloads"></a>
  <a href="https://pypi.org/project/cairnml-agent/"><img src="https://img.shields.io/pypi/pyversions/cairnml-agent?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://github.com/gianlucapargaetzi/cairnml-agent/stargazers"><img src="https://img.shields.io/github/stars/gianlucapargaetzi/cairnml-agent?style=flat-square&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/gianlucapargaetzi/cairnml-agent/blob/main/LICENSE"><img src="https://img.shields.io/github/license/gianlucapargaetzi/cairnml-agent?style=flat-square&color=green" alt="License"></a>
</p>

# CairnML Agent

CairnML Agent runs the jobs of a CairnML queue on this machine. It takes one job at a time from its queue, prepares the job's environment, runs it and reports the result back to the server.

## Features

- Takes jobs from one or more queues.
- Runs each job in its own Docker container. Docker is required; jobs in a Python virtual environment on the host need `--allow-venv`, which is off by default.
- Gives the agent the GPUs you choose with `--gpus`.
- Gives every job its own short-lived token, so a job never receives the agent's own token.
- Is enrolled once by an admin, who signs in when the browser opens.

## Prerequisites

- Python 3.9 or newer
- A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server)
- An admin account on that server, since only admins can enrol an agent
- Docker (required; see `--allow-venv` for the exception)

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install cairnml-agent
```

## Connect to your server

```bash
cairnml-agent init http://cairnml
```

The argument is the server's address. The web app is at `<address>/cairnml` and the API at `<address>/cairnml/api`, which the command works out itself, so there are no ports to give. Your browser opens the sign-in page: sign in as an admin. The browser sign-in receives its reply on `127.0.0.1:8765`, the only redirect address the identity provider accepts; if another program holds that port, the command signs in with a code instead. A non-admin is refused, and no configuration is written. The configuration is stored in `~/cairnml-agent.conf`; the agent's git credentials and extra package indexes are set there by hand.

The agent enrols as its own device, with its own key (`~/.cairnml/agent/device.key`), apart from the SDK's `cairnml init`. `--name` names the machine, `--force` enrols it again (the previous device is revoked) and `-F <file>` uses another configuration file.

## Start the agent

```bash
cairnml-agent daemon --queue default
```

| Option | What it does |
|---|---|
| `--queue NAME` | The queue to take jobs from |
| `--docker` | Run each job in a container (required unless `--allow-venv` is given) |
| `--allow-venv` | Allow jobs in a Python virtual environment on this host. Off by default: such a job can read the agent's device key |
| `--gpus 0` | The GPUs the agent may use |

Run `cairnml-agent daemon --help` for the rest.

## Trust model

The agent runs the code of every job that reaches its queue, so decide who may enqueue jobs before you start it.

- **Docker (`--docker`) is the isolated mode.** A job runs in its own container, which does not see the agent's device key (`~/.cairnml/agent/device.key`). Use it for any setup where jobs come from more than one person.
- **Virtual environment (`--allow-venv`) is not isolated.** A job runs as the agent's own user, so it can read the device key. The key signs in as the admin who enrolled the agent, with a token that lasts until the device is revoked. The agent prints a warning when it starts this way. Use it only for code you fully trust.
- A task's own Docker arguments cannot give its container host access. By default the agent drops switches such as `--privileged`, `--cap-add`, `--device`, host namespaces (`--pid`, `--ipc`, `--network`), mounts and volumes (`-v`, `--mount`), and host files (`--env-file`, `--log-opt`). The list is `forbidden_docker_args` in the agent configuration. Changing it is an administrator decision, and an empty list turns the check off.
- Keep the agent's own folder (`~/.cairnml/agent`) out of any cache or mount you configure for jobs.

## Check it

The **Workers** page of the web app lists this machine once it is running. Send a job to the queue, for example with `Task.enqueue` from the [SDK](https://github.com/gianlucapargaetzi/cairnml), and it starts on this machine.

## Next steps

Continue with the [CairnML examples](https://github.com/gianlucapargaetzi/cairnml-examples).

## Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml-agent/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml-agent/issues/new) with the command you ran and the full error message.

## License

Apache License 2.0, see [LICENSE](LICENSE). Unofficial fork of ClearML Agent, see [DISCLAIMER.md](DISCLAIMER.md).
