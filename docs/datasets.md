# Datasets: `clearml-dataset`

A dataset is a git repository with a `data/` folder. Git holds the history, DVC holds the files in object
storage, and the **commit of the repository is the dataset version**. A training run records the repository,
the commit and a hash of the files, so you can always tell which data a run used.

Install: `pip install "clearml[dataset]"` (needs Python 3.9+ and `git`).

## Your own data

Create an empty repository on GitHub and clone it. Then, inside it:

```bash
clearml-dataset init                 # git tracking, file storage, dataset.yaml and the footprint check
cp -r /path/to/measurements data/    # put your files in data/
clearml-dataset status               # branch, and what is new, changed or deleted
clearml-dataset add                  # record the state of data/, deleted files included
clearml-dataset commit -m "March measurements"   # checks the data, writes dataset.lock.json, commits
clearml-dataset push                 # files first, then the commit, through a branch and a pull request
clearml-dataset register             # once: add the dataset to the registry
```

`add` records the whole of `data/` as it is now: new and changed files are included and files you removed are
removed from the dataset. Older commits keep their files; check one out with `Dataset.get`.

### Native or through an adapter

A dataset must be readable in the standard format, so every dataset says how. `dataset.yaml` holds one of two lines:

- `format: native`: `data/` already contains `samples.parquet` and `manifest.json`, the standard sample table (see
  `clearml.datasets.schema`).
- `adapter: <name>` (and optionally `registry: <url>`): an adapter of the registry reads the files in `data/` into it.
  `clearml-dataset init --adapter <name>` sets this up.

`clearml-dataset check` reads `data/` as `dataset.yaml` says and validates the result. A dataset that is neither native
nor read by an adapter cannot be committed.

### The footprint

`commit` writes `dataset.lock.json` next to `data.dvc`. It copies the size, file count and content hash that DVC wrote,
says in which format the data was read, and ends with a checksum of everything above. `.clearml/verify.py` recomputes
what it can from git alone (no data, no server, no key): that the lock and `data.dvc` agree, that the checksum holds,
that `dataset.yaml` and the lock name the same format, and that the repository holds only the files a dataset
repository may hold. A GitHub Action (`.github/workflows/dataset.yml`, written by `init`) runs it.

It is a footprint, not a signature: someone who writes a matching lock by hand gets past it. That fails when the
dataset is used, because the data is read and validated again.

### Publishing

Set a ruleset on the default branch of the dataset repository: pull requests only, no force pushes, and the check
`verify` required. `push` then sends a commit made on the default branch to `incoming/<commit>` and opens a pull
request with auto-merge when the GitHub command line (`gh`) is installed; otherwise it prints the link. The first push
to an empty repository goes straight to its first branch. `push` refuses a commit whose footprint does not hold.

### The registry

`clearml-dataset register` asks the server to add the dataset to the registry. The server reads the dataset
repository's default branch with the GitHub App, verifies the footprint, and opens a pull request in the registry with
the entry. It happens once: a repository that is registered, or waiting in a pull request, is refused. The commit must
already be on the default branch. The server needs `apiserver.dataset_registry.repository` and `allowed_owners`, and
the GitHub App needs write access to contents and pull requests on the registry.

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

A separate repository, `external-datasets`, is the registry. It holds one small file per dataset and the adapters.
Datasets of our own have an entry too (`source: repo`, see above); the others look like this:

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
read-only GitHub token for the registry and for the dataset repositories its entries point to, and for no other
repository. The registry is the only list of dataset repositories; the server reads its default branch.
