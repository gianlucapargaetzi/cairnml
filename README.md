# CairnML

CairnML records what your training scripts do (parameters, metrics, logs, artifacts), runs them on remote machines through job queues, and versions datasets with git and DVC so every run records the exact data it used.

This is the Python SDK. It needs a CairnML server (see [cairnml-server](https://github.com/gianlucapargaetzi/cairnml-server) and the [deployment files](https://github.com/gianlucapargaetzi/cairnml-stack)).

CairnML is an unofficial, modified version of [ClearML](https://github.com/clearml/clearml). It is not created, sponsored, endorsed or supported by ClearML, Inc. See [DISCLAIMER.md](DISCLAIMER.md) and [NOTICE](NOTICE).

## Install

```bash
pip install cairnml
pip install "cairnml[dataset]"   # adds the dataset tools (DVC, Parquet)
```

## Use

Sign this machine in to your server once:

```bash
cairnml-init --server https://your-server:8008
```

Track a script:

```python
from cairnml import Task

task = Task.init(project_name="demo", task_name="first run")
task.connect({"lr": 0.001})
task.get_logger().report_scalar("loss", "train", value=0.5, iteration=1)
```

Run a recorded task on an agent ([cairnml-agent](https://pypi.org/project/cairnml-agent/)):

```python
Task.enqueue(task, queue_name="default")
```

Version a dataset (a dataset is a git repository, its commit is its version):

```bash
cairnml-dataset init
cairnml-dataset add
cairnml-dataset commit -m "March measurements"
cairnml-dataset push
```

See [docs/datasets.md](docs/datasets.md) for datasets, adapters and the cache.

## License

Apache License 2.0, see [LICENSE](LICENSE). Based on ClearML, Copyright 2025 ClearML Inc.
