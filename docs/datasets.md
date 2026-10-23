# Datasets: `clearml-dataset`

A dataset is a git repository with a `data/` folder. Git holds the history, DVC holds the files in object
storage, and the **commit of the repository is the dataset version**. A training run records the repository,
the commit and a hash of the files, so you can always tell which data a run used.

Install: `pip install "clearml[dataset]"` (needs Python 3.9+ and `git`).

## Your own data

Create an empty repository on GitHub and clone it. Then, inside it:

```bash
clearml-dataset init                 # git tracking and file storage are set up
cp -r /path/to/measurements data/    # put your files in data/
clearml-dataset status               # branch, and what is new, changed or deleted
clearml-dataset add                  # record the state of data/, deleted files included
clearml-dataset commit -m "March measurements"
clearml-dataset push                 # files first, then the commits
```

`add` records the whole of `data/` as it is now: new and changed files are included and files you removed are
removed from the dataset. Older commits keep their files; check one out with `Dataset.get`.

## Using a dataset in a script

```python
from clearml import Task
from clearml.datasets import Dataset

task = Task.init(project_name="cars", task_name="train")
data = Dataset.get("https://github.com/acme/cars-data.git", "3f2a9c1")   # commit, tag or branch
print(data.path)            # a folder with the files
```

The files are downloaded once into your cache and reused by every later run on the machine. The task gets
the parameters `Datasets/<name>/repo`, `commit` and `content_hash`. A commit that is not pushed is refused.
Use a commit hash, not a branch, when you want to rerun the same data later.

## Datasets from outside (Hugging Face, git, local folders)

A separate repository, `external-datasets`, holds one small file per dataset and the adapters:

```yaml
# datasets/coco-det.yaml
source: huggingface
link: hf://datasets/org/coco-det
adapter: coco_det                # the default adapter
revision: null                   # optional: pin the source's own commit
overrides:                       # a different adapter for revisions where the layout changed
  - revisions: ["9b1c4e7"]
    adapter: coco_det_v2
```

```python
data = Dataset.get_external("coco-det")                    # latest commit of external-datasets
data = Dataset.get_external("coco-det", "a1b2c3d")         # a fixed commit, to rerun an earlier run exactly
for sample in data.samples(split="train"):
    image = sample.modality("image")        # a path
    boxes = sample.label("bbox2d")          # [{"box": [x1, y1, x2, y2], "label": "car"}, ...]
```

The commit of `external-datasets` fixes the pin file and the adapter code. Without one, the latest commit of its
default branch is used; that branch is kept working by the repository's tests. The source's revision is the one
you pass, else the pin file's, else the latest. Whatever was used is recorded on the run
(`Datasets/<name>/revision`, `adapter`, `repo_commit`), so a rerun can pass the same values. Name the repository with `repo=...` or the
configuration key `sdk.datasets.external_repo`.

Every adapter produces the same shape: samples with named modalities (`image`, `depth`, ...) and typed labels
(`class`, `bbox2d`, `mask2d`, `json`). Boxes are always `[x1, y1, x2, y2]` in pixels. An adapter that finds a
layout it does not know stops the run with a message that names the adapter. To write one, see the docstring of
`clearml.datasets.adapter`.

## The cache

Datasets are kept per user, by default in `~/.clearml/cache/datasets`, up to 50 GB. When the cache is over
its limit, the versions used longest ago are removed first; a version in use is never removed.

```bash
clearml-dataset cache info
clearml-dataset cache limit 20GB
clearml-dataset cache clean
```

```
sdk.datasets.cache.dir: ~/.clearml/cache/datasets
sdk.datasets.cache.max_size: 50GB
```

On an agent, mount the cache into job containers so it survives between jobs, for example in the agent's
`clearml.conf`: `agent.extra_docker_arguments: ["-v", "/srv/dataset-cache:/root/.clearml/cache/datasets"]`.

## Access

DVC gets short-lived keys for the dataset bucket from the server (`datachunks.dataset_credentials`); no access
key is stored on your machine. On your machine, git uses your own login. In a job, the server gives the agent a
read-only GitHub token for the dataset repositories listed in its `apiserver.idp.dataset_repositories` setting.
