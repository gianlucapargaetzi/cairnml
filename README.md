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

# CairnML

CairnML records what your training scripts do, runs them on other machines, and versions the data they use. This Python package is the SDK for your scripts; the extra `cairnml[agent]` adds the agent that runs queued jobs.

The setup order: a **server** runs (usually already set up for you), then you install the **SDK** and enroll it, optionally install and enroll an **agent** on the machine that runs jobs, and use the **web app** in your browser. [Start here](docs/start-here.md) explains the four parts in more detail.

## Features

- **Track** a script with a few lines: parameters, metrics, logs and artifacts are recorded on your server.
- **Run remotely:** send a run to an agent on another machine through a job queue. The agent runs each job in Docker.
- **Version datasets** with git, with the files in DVC storage or GitHub LFS. Every run records the exact data commit it used.
- **Read public datasets** from the [dataset registry](https://github.com/gianlucapargaetzi/cairnml-dataset-registry).
- **Browse everything** in the web app: projects, runs, datasets, workers and queues.

## Prerequisites

- Python 3.9 or newer
- A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server)
- git, for datasets
- Docker, on the machine that runs an agent. For GPU jobs the machine also needs the NVIDIA driver and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

> [!IMPORTANT]
> You need a login on the server before you can enroll anything. Accounts are created by an administrator of the server: ask them for one before you start. Without it, `cairnml init` and `cairnml-agent init` cannot sign you in.

> [!TIP]
> To check that Docker can use the GPU, run `docker run --rm --gpus all ubuntu nvidia-smi`. If it prints your GPU, jobs can use it.

## Installation

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install cairnml
```

Add an extra for what you need, for example `pip install "cairnml[agent]"`:

| Extra | Adds |
|---|---|
| `cairnml[agent]` | the agent: `cairnml-agent`, which runs queued jobs on this machine |
| `cairnml[dataset]` | the dataset tools (`cairnml dataset ...`) |
| `cairnml[plot]` | matplotlib figures as interactive plotly plots (without it, figures are sent as images) |

## Enrollment

Every machine enrolls once, with its own key. No access key is copied between machines.

**SDK**, on every machine whose scripts you want to track:

```bash
cairnml init http://<server-ip>
```

`<server-ip>` is the address of your server, as your team gave it to you (on the machine that runs the server, `http://localhost`). Add a port only if the server does not use port 80. Your browser opens the sign-in page; when the command prints `enrolled as ...`, the machine is connected.

**Agent**, on the machine that should run jobs:

```bash
cairnml-agent init http://<server-ip>
cairnml-agent daemon --docker
```

`init` makes a queue for this machine and prints its name. `daemon` starts the agent, which serves that queue and keeps this terminal busy until you stop it (see the tip on `--detached` below).

> [!IMPORTANT]
> An agent serves only the queue made when it was enrolled. Send its jobs to that queue. **Workers & Queues** in the web app lists the name.

Check that it worked:

| Check | Command or place | You should see |
|---|---|---|
| SDK is enrolled | `cairnml whoami` | the user this machine signs in as |
| SDK is healthy | `cairnml doctor` | one line per check, exit status 0 |
| Agent is healthy | `cairnml-agent doctor` | one line per check, including Docker |
| Agent is running | web app, **Workers & Queues** | this machine in the list of workers |

> [!TIP]
> On a machine without a browser, add `--device` to `init`: it shows a code and an address. Open the address on another device, sign in and enter the code.

> [!TIP]
> `cairnml-agent daemon --docker --detached` runs the agent in the background (not on Windows); `cairnml-agent daemon --docker --stop` stops it. Without `--gpus` the agent's jobs may use every GPU; `--gpus 0` limits it to GPU 0, `--cpu-only` to none.

> [!WARNING]
> Jobs run in Docker, which keeps them away from the agent's key. `--allow-venv` runs jobs on the host instead, where they can read that key. Use it only for code you fully trust (see the [agent README](docs/agent/README.md)).

`cairnml logout` removes this machine's enrollment. `cairnml devices list` and `cairnml devices revoke <id>` manage the devices of your account.

## Getting started

Track a script:

```python
from cairnml import Task

task = Task.init(project_name="demo", task_name="first run")
task.connect({"lr": 0.001})
task.get_logger().report_scalar("loss", "train", value=0.5, iteration=1)
```

Run it on an agent, in the queue the agent printed at enrollment:

```python
task.execute_remotely(queue_name="<your-queue>")
```

The agent takes the task's git repository, commit and uncommitted changes from the record and runs it in a Docker container: in the image you set with `task.set_base_docker(...)`, or in the agent's default image. The run (called a *task* in the web app) appears under its project, with its parameters, metrics and artifacts. A queued job waits until an agent takes it; `execute_remotely` warns when no agent listens to the queue, for example after a typing error in its name.

## Web app

Open `http://<server-ip>/cairnml/` and sign in.

| Section | What it shows |
|---|---|
| **Dashboard**, **Projects** | projects, and the tasks and models of each |
| **Datasets** | dataset versions |
| **Pipelines**, **Reports** | pipelines, and written reports |
| **Workers & Queues** | the machines running agents, and the jobs waiting for them |
| **Settings** | access and credentials |

> [!TIP]
> Select two or more tasks in a project and click **Compare** to see which parameters, code and metrics differ.

## Datasets

A dataset is a git repository: git holds the metadata, and its commit is the version of the data. The files are in DVC storage on your server (`storage: s3`, the default) or in GitHub LFS (`storage: lfs`).

### Access

```python
from cairnml.datasets import Dataset

data = Dataset.get("https://github.com/acme/cars-data.git", "3f2a9c1")  # a commit, tag or branch
print(data.path)  # a local folder with the files
```

A public dataset from the registry is read through an adapter that turns its files into samples:

```python
data = Dataset.get_external(
    "coco-fashion",
    repo="https://github.com/gianlucapargaetzi/cairnml-dataset-registry",
)
for sample in data.samples("train"):
    image = sample.modality("image")   # a path
    boxes = sample.label("bbox2d")     # [{"box": [x1, y1, x2, y2], "label": "shoe"}, ...]
```

Both calls record the dataset and its revision on the run.

> [!IMPORTANT]
> A private dataset needs two kinds of access.
> - **Its repository:** a dataset is read with the credential it was created with (`--credential <id>`). Credentials are GitHub App credentials, created in the web app under **Settings > Access**; `cairnml credential list` shows the ones you can use. A dataset without a credential must be in a public repository.
> - **Its files:** for `storage: s3` you must have access to the storage that holds them. For a dataset created with the same server you use, this is handled automatically: the server hands out short-lived keys.

### Management

```bash
cairnml dataset create cars-data --from ./measurements --storage lfs
cairnml dataset doctor   # checks the GitHub settings the dataset needs
```

`create` needs `cairnml[dataset]` and the GitHub command line (`gh auth login`). It makes a private repository, checks and commits the files, pushes them, protects the default branch and prints `Created <url>`: that URL is what `Dataset.get` takes. The full workflow (changes, publishing, credentials) is in [docs/datasets.md](docs/datasets.md).

> [!WARNING]
> The default branch of a dataset repository takes changes only through pull requests that pass the `verify` check. Do not push to it directly.

## Next steps

- The [CairnML examples](https://github.com/gianlucapargaetzi/cairnml-examples) walk through a first run, a full training run, and a dataset from the registry.
- [docs/agent/README.md](docs/agent/README.md): the agent in detail, including its trust model.
- [docs/environment.md](docs/environment.md): every `CAIRNML_*` environment variable.

## Having problems

1. Run `cairnml doctor` (and `cairnml-agent doctor` on an agent machine): it names the failing check and the next step.
2. A server problem (sign-in, storage, queues) belongs in the [server repository](https://github.com/gianlucapargaetzi/cairnml-server/issues). For everything else, check the [open issues](https://github.com/gianlucapargaetzi/cairnml/issues).
3. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml/issues/new) with the command you ran, your Python version and the full error message.

## License

Apache License 2.0, see [LICENSE](LICENSE). Unofficial fork of ClearML, see [DISCLAIMER.md](DISCLAIMER.md).
