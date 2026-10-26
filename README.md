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

# 🪨 CairnML SDK

Track your training runs, version your datasets and send jobs to remote machines.

## 🧰 Prerequisites

- 🐍 Python 3.9 or newer
- 🔀 `git`, for datasets only
- 🖥️ A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server)

## 📦 Installation

```bash
pip install cairnml
```

For the dataset tools:

```bash
pip install "cairnml[dataset]"
```

## 🔌 Connect to your server

```bash
cairnml-init --server http://<server-ip>:8008
```

Your browser opens the sign-in page. When the command prints `enrolled as …`, this machine is connected.

## 🚀 Getting started

Track a script:

```python
from cairnml import Task

task = Task.init(project_name="demo", task_name="first run")
task.connect({"lr": 0.001})
task.get_logger().report_scalar("loss", "train", value=0.5, iteration=1)
```

Run it on an agent:

```python
Task.enqueue(task, queue_name="default")
```

## 🖥️ The web app

Open `http://<server-ip>:8080` and sign in. The menu has these sections:

- 🏠 **Dashboard:** projects and recent experiments
- 📁 **Projects:** the tasks, experiments and models of each project
- 🗂️ **Datasets:** dataset versions
- 🔀 **Pipelines**
- 📝 **Reports**
- 🖥️ **Workers & Queues:** the machines running agents, and the jobs waiting for them
- ⚙️ **Settings**

## 📂 Access your data

```python
from cairnml.datasets import Dataset

data = Dataset.get("https://github.com/acme/cars-data.git", "3f2a9c1")  # a commit, tag or branch
print(data.path)  # a local folder with the files
```

To create your own dataset, see [docs/datasets.md](docs/datasets.md).

## ✅ Setup complete

Your SDK is connected. Continue with **[cairnml-examples](https://github.com/gianlucapargaetzi/cairnml-examples)**.

## 🆘 Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml/issues/new) with the command you ran and the full error message.

## 📄 License

Apache License 2.0, see [LICENSE](LICENSE). Unofficial fork of ClearML, see [DISCLAIMER.md](DISCLAIMER.md).
