# CairnML agent

The agent takes jobs from a CairnML queue and runs them on this machine: it prepares the environment, runs the task and reports back. It works with a [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server) and the [cairnml SDK](https://pypi.org/project/cairnml/).

CairnML is an unofficial, modified version of [ClearML](https://github.com/clearml/clearml). It is not created, sponsored, endorsed or supported by ClearML, Inc. See [DISCLAIMER.md](DISCLAIMER.md) and [NOTICE](NOTICE).

## Install

```bash
pip install cairnml-agent
```

## Use

Sign the machine in to your server once, then listen to a queue:

```bash
cairnml-agent init
cairnml-agent daemon --queue default
```

Add `--docker` to run each job in a container and `--gpus 0` to give the agent chosen GPUs.

## License

Apache License 2.0, see [LICENSE](LICENSE). Based on ClearML Agent, Copyright 2025 ClearML Inc.
