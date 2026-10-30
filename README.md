<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/cairnml-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/cairnml-logo.svg">
    <img src="assets/cairnml-logo.svg" alt="CairnML" width="480">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/gianlucapargaetzi/cairnml/actions/workflows/ci.yml"><img src="https://github.com/gianlucapargaetzi/cairnml/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/cairnml/"><img src="https://img.shields.io/pypi/v/cairnml?style=flat-square&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://pypi.org/project/cairnml/"><img src="https://img.shields.io/pypi/dm/cairnml?style=flat-square&label=monthly%20downloads" alt="Monthly downloads"></a>
  <a href="https://pypi.org/project/cairnml/"><img src="https://img.shields.io/pypi/pyversions/cairnml?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://github.com/gianlucapargaetzi/cairnml/stargazers"><img src="https://img.shields.io/github/stars/gianlucapargaetzi/cairnml?style=flat-square&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/gianlucapargaetzi/cairnml/blob/main/LICENSE"><img src="https://img.shields.io/github/license/gianlucapargaetzi/cairnml?style=flat-square&color=green" alt="License"></a>
</p>

# CairnML SDK

The CairnML SDK is the Python library you add to your training scripts. It records what a run does (parameters, metrics, logs and artifacts), sends runs to remote agents through job queues, and versions datasets so that every run knows the exact data it used.

New to CairnML? Read [Start here](docs/start-here.md) first: it explains the four parts (server, SDK, agent, web app), which are required, and the order of setup.

## Features

- Track a training script with a few lines: parameters, metrics, logs and artifacts are recorded on your server.
- Send a recorded run to an agent on another machine through a job queue.
- Version datasets with git and DVC, and record the dataset commit on every run.
- Read public datasets through adapters from the [dataset registry](https://github.com/gianlucapargaetzi/cairnml-dataset-registry), with the source revision recorded.

## Prerequisites

- Python 3.9 or newer
- git, for datasets
- A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server)

## Installation

```bash
pip install cairnml
```

For the dataset tools as well:

```bash
pip install "cairnml[dataset]"
```

## Connect to your server

```bash
cairnml init http://<server-ip>
```

`<server-ip>` is the address of the server, for example `http://localhost` or `http://192.168.1.20`. Add a port only if the server does not use port 80. The command works out where the API and the web app are (`<address>/cairnml/api` and `<address>/cairnml/`).

Your browser opens the sign-in page. When the command prints `enrolled as ...`, this machine is connected. Each machine has its own key, so no access key is copied to it.

The browser sign-in receives its reply on `127.0.0.1:8765`. The identity provider accepts only this redirect address for the CairnML client, so the port cannot be changed. If another program on the machine holds port 8765, `cairnml init` says so and signs in with a code instead (see `--device`). On a machine without a browser, `cairnml init --device` always uses the code.

Environment variables named `TRAINS_*` (from the ClearML-based original) still work. Each one prints a deprecation warning once; use the `CAIRNML_*` name of the same setting.

## Commands

Everything is one command, `cairnml`, with a subcommand for each tool. `cairnml <command> --help` shows the arguments of a command.

| Command | What it does |
|---|---|
| `cairnml init <server>` | Enrol this machine with the server. `--name` names the machine, `--force` enrols again. |
| `cairnml whoami` | Show who this machine signs in as |
| `cairnml logout` | Revoke this machine's enrolment and remove its key |
| `cairnml dataset ...` | Versioned datasets on git and DVC (needs `cairnml[dataset]`) |
| `cairnml task ...` | Launch any codebase on a machine running `cairnml-agent` |
| `cairnml param-search ...` | Search for the best parameters for your models |
| `cairnml debug ...` | Debugging tools for the SDK |

## Getting started

Track a script:

```python
from cairnml import Task

task = Task.init(project_name="demo", task_name="first run")
task.connect({"lr": 0.001})
task.get_logger().report_scalar("loss", "train", value=0.5, iteration=1)
```

Run the recorded task on an agent:

```python
Task.enqueue(task, queue_name="default")
```

The run appears in the web app under its project, with its parameters, metrics and artifacts.

A task waits in its queue until a worker listens to it. `Task.enqueue` logs a warning when no worker listens to the queue, for example after a typing error in its name. To check it yourself:

```python
from cairnml import queues

for queue in queues.list_queues():
    print(queue.name)

print(queues.workers_for_queue("default"))  # [] when no agent listens to it
```

## The web app

Open `http://<server-ip>/cairnml/` and sign in. The menu has these sections:

- **Dashboard:** projects and recent experiments
- **Projects:** the tasks, experiments and models of each project
- **Datasets:** dataset versions
- **Pipelines** and **Reports**
- **Workers & Queues:** the machines running agents, and the jobs waiting for them
- **Settings**

## Access data

Read a dataset from a repository of your own, at a commit:

```python
from cairnml.datasets import Dataset

data = Dataset.get("https://github.com/acme/cars-data.git", "3f2a9c1")  # a commit, tag or branch
print(data.path)  # a local folder with the files
```

Read a public dataset from the dataset registry. The adapter turns its files into samples with images and labelled boxes:

```python
from cairnml.datasets import Dataset

data = Dataset.get_external(
    "coco-fashion",
    repo="https://github.com/gianlucapargaetzi/cairnml-dataset-registry",
)
for sample in data.samples("train"):
    image = sample.modality("image")   # a path
    boxes = sample.label("bbox2d")     # [{"box": [x1, y1, x2, y2], "label": "shoe"}, ...]
```

Both calls record the dataset and its revision on the run. To create a dataset of your own, see [docs/datasets.md](docs/datasets.md).

## Next steps

The [CairnML examples](https://github.com/gianlucapargaetzi/cairnml-examples) walk through a first run, a full training run, and the same training on a dataset from the registry.

## Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml/issues/new) with the command you ran, your Python version and the full error message.

## License

Apache License 2.0, see [LICENSE](LICENSE). Unofficial fork of ClearML, see [DISCLAIMER.md](DISCLAIMER.md).
