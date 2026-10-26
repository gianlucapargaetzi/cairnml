# 🤖 CairnML Agent

Runs the jobs of a CairnML queue on this machine.

## 🧰 Prerequisites

- 🐍 Python 3.9 or newer
- 🖥️ A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server)
- 👤 An admin account on that server, since only admins enrol agents
- 🐳 Docker, for jobs that run in containers (optional)

## 📦 Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install cairnml-agent
```

## 🔌 Connect to your server

```bash
cairnml-agent init
```

Your browser opens the sign-in page. Sign in as an admin.

## 🚀 Start the agent

```bash
cairnml-agent daemon --queue default
```

| Option | What it does |
|---|---|
| `--queue NAME` | The queue to take jobs from |
| `--docker` | Run each job in a container |
| `--gpus 0` | The GPUs the agent may use |

The **Workers** page of the web app lists this machine once it is running.

## ✅ Setup complete

Your agent is ready to run jobs. Continue with **[cairnml-examples](https://github.com/gianlucapargaetzi/cairnml-examples)**.

## 🆘 Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml-agent/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml-agent/issues/new) with the command you ran and the full error message.

## 📄 License

Apache License 2.0, see [LICENSE](LICENSE). Unofficial fork of ClearML Agent, see [DISCLAIMER.md](DISCLAIMER.md).
