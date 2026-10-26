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
- Runs each job in its own container with `--docker`.
- Gives the agent the GPUs you choose with `--gpus`.
- Gives every job its own short-lived token, so a job never receives the agent's own token.
- Is enrolled once by an admin, who signs in when the browser opens.

## Prerequisites

- Python 3.9 or newer
- A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server)
- An admin account on that server, since only admins can enrol an agent
- Docker, for jobs that run in containers (optional)

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install cairnml-agent
```

## Connect to your server

```bash
cairnml-agent init
```

The command runs a short wizard. Your browser opens the sign-in page: sign in as an admin. A non-admin is refused, and no configuration is written.

## Start the agent

```bash
cairnml-agent daemon --queue default
```

| Option | What it does |
|---|---|
| `--queue NAME` | The queue to take jobs from |
| `--docker` | Run each job in a container |
| `--gpus 0` | The GPUs the agent may use |

Run `cairnml-agent daemon --help` for the rest.

## Check it

The **Workers** page of the web app lists this machine once it is running. Send a job to the queue, for example with `Task.enqueue` from the [SDK](https://github.com/gianlucapargaetzi/cairnml), and it starts on this machine.

## Next steps

Continue with the [CairnML examples](https://github.com/gianlucapargaetzi/cairnml-examples).

## Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml-agent/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml-agent/issues/new) with the command you ran and the full error message.

## License

Apache License 2.0, see [LICENSE](LICENSE). Unofficial fork of ClearML Agent, see [DISCLAIMER.md](DISCLAIMER.md).
