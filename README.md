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
cairnml-init --server http://cairnml:8008
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

Open `http://cairnml:8080` and sign in. The menu has these sections:

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
