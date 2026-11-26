# Datasets

CairnML versions the datasets your runs use. A dataset of your own is a git repository, so every run records the exact commit and content it used. Public datasets are read through adapters from the [dataset registry](https://github.com/gianlucapargaetzi/cairnml-dataset-registry), which turns each source into the same sample format.

```bash
pip install "cairnml[dataset]"
```

You also need git and a running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server).

## How a dataset is stored

Git holds the metadata: `dataset.yaml` says how the files are read and where they are stored, and `dataset.lock.json` is the footprint, the record that the data was checked. The files are held in one of two places, chosen once by `cairnml dataset remote add` and recorded in `dataset.yaml`:

- `storage: s3` (the default): DVC holds the files in the dataset storage behind the CairnML server. Git also holds `data.dvc`, which lists the files in `data/` by their content hash. `push` uploads the files before it publishes the commit, so a commit on GitHub never refers to files that are not stored.
- `storage: lfs`: the files are in the GitHub LFS of the dataset repository. No DVC and no storage keys are involved: a clone needs git and git-lfs only. The footprint records the LFS object of every file.

Choose `--lfs` on `remote add` when the repository can hold the files (GitHub LFS has its own storage and bandwidth limits). Choose the default for data that should live in the server's bucket. The storage cannot be changed afterwards.

- **The commit is the version.** A run records the repository, the commit and the content hash of the data it used. Pass a commit when you want to rerun the same data later.
- **Old versions keep their files.** Removing a file makes a new commit; the earlier commit still refers to the old file.
- **The checks need no data.** The footprint says what the data is, so the `verify` check in the repository reads git only.

A repository is a dataset when its root holds `dataset.yaml` at the commit you read. `DATASET.md` is optional: the web app shows it as the dataset's page.

## Create a dataset of your own

The shortest way is one command. It creates a private repository on GitHub with the GitHub command line (`gh`, logged in with `gh auth login`), copies the files in, checks them, commits and pushes:

```bash
cairnml dataset create cars-data --from /path/to/measurements --storage lfs
cairnml dataset doctor                   # once: check the GitHub settings
```

`--public` makes the repository public, `--path` chooses the local folder (default: the name), and `--adapter` and `--registry` say how the data is read (see below). With `--adapter local`, the `adapter.py` in the `--from` folder goes to the top of the repository and the other files go to `data/`. If the check fails, the command stops with a message: fix the data and run `cairnml dataset commit` and `cairnml dataset push` in that folder.

Right after the first push, `create` protects the default branch: pull requests are required (no approvals needed, and administrators are not held to them), and force pushes and deletions are blocked. This needs **administration rights on the repository**. Without them, `create` still publishes the repository and warns which permission is missing. Pass `--no-protect` to skip the step and protect the branch yourself, as `cairnml dataset doctor` describes.

To set a dataset up by hand, create an empty repository on GitHub, then, in a new folder:

```bash
cairnml dataset init --adapter local     # a git repository, and how data/ is read. No remote and no storage yet
cp -r /path/to/measurements data/        # copy your files into data/ (and adapter.py to the top, for --adapter local)
cairnml dataset remote add <url> --credential <id>   # origin, the storage (s3, or --lfs), the dataset on the server. Once
cairnml dataset status                   # what is new, changed or deleted
cairnml dataset add                      # record all of data/ as it is now: new, changed and removed files
cairnml dataset commit -m "March measurements"   # check the data and commit it with its footprint
cairnml dataset doctor                   # once: check the set-up and the GitHub settings
cairnml dataset push                     # send the files, then the commit, as a pull request
```

`init` and the other local commands never reach the network or the server; only `remote add` and `push` do. `add`, `commit` and `push` refuse until `remote add` has run.

### Credentials

A dataset is read with the credential it was added with, and with no other. The credential is one of your own GitHub Apps, or the company's GitHub App:

```bash
cairnml credential list                  # the id, type, name, scope (yours or company), owner and state of each
```

- `remote add --credential <id>` (and `create --credential <id>`) gives the credential the server reads the dataset with. It checks that the repository can be reached with it; if not, it prints a warning, not an error. Choose another credential, or install your organisation's GitHub App on the repository (see Access).
- A dataset with **no credential is public**. If the repository is private and has none, `remote add` warns, and the dataset cannot be read until one is added.
- `cairnml dataset add-credential <id>` sets the credential later. Its owner or an admin may do it. With no id it makes the dataset public. The web app cannot set it yet.
- A job reads a dataset with the dataset's own credential, and no other, and only when the person who queued the job can see the dataset or its project holds it through an allocation. Project grants on credentials do not apply to datasets.

## Cloning a dataset

`cairnml dataset clone <reference> [folder] [--commit <commit>]` makes a working copy of a dataset you may see: its repository, checked out at the commit, and its data in `data/` (git does not track that folder). The server decides whether you may see the dataset and gives the read token of its credential; a public repository needs no token. Once your access ends, the copy stays readable on your machine, but the server refuses its changes.

Without CairnML, a dataset in LFS is fetched with `git clone <repository>`, `git checkout <commit>` and `git lfs pull` (`Dataset.fetch_commands()` returns these commands). A dataset in S3 is fetched with `cairnml dataset get <repository> <commit>`, which holds the keys.

## Names and ids

Every dataset has a unique id on the server and a name, its **reference**, unique in the company: a repository's name (`cars`), or `<registry>/<name>` for an entry of a registry. A run names a dataset by its reference, id or repository address:

```python
Dataset.get("cars", commit)                                  # by reference
Dataset.get("3f9c0e1a...", commit)                           # by id
Dataset.get("https://github.com/acme/cars.git", commit)      # by repository address
```

A job resolves what its task's user may see, or what its project holds. A run records the id and the reference of each dataset it used, next to the repository and the commit. A second dataset with a name already in use is refused when it is added.

## How the data is read

`dataset.yaml` says how a dataset's files are read, in one of three forms:

- `format: native`: `data/` already contains the standard sample table, `samples.parquet` with `manifest.json`.
- `adapter: <name>`, with `registry: <url>`: an adapter from the registry reads the files in `data/` into the standard format. `cairnml dataset init --adapter <name> --registry <url>` writes this line for you.
- `adapter: local`: the adapter is `adapter.py` at the top of the repository, and no registry is needed. `cairnml dataset init --adapter local` writes this line for you.

`cairnml dataset check` reads `data/` the way `dataset.yaml` says and reports what is wrong. A dataset that is neither native nor read by an adapter cannot be committed.

### The local adapter

A local adapter is a file of your own. It defines one `Adapter` subclass, with `modalities`, `labels` and `run`, exactly as a registry adapter does. For example, for a `data/cars.csv` with the columns `id,split,image,class` (image paths relative to `data/`):

```python
# adapter.py, at the top of the dataset repository
import csv
from cairnml.datasets import Adapter

class CarsCsv(Adapter):
    modalities = {"image": "image"}
    labels = {"class": "class"}

    def run(self, source, writer):
        with open(source / "cars.csv", newline="") as handle:
            for row in csv.DictReader(handle):
                writer.add(row["id"], row["split"], {"image": row["image"]}, {"class": row["class"]})
```

The adapter runs only when you check the data on your machine (`check`, `commit` and `create`). The footprint records the sha256 of `adapter.py`, so a change to the adapter after the check is found by `push` and by the repository's CI check, and the data must be checked again. Reading a dataset with `Dataset.get` never runs the adapter.

## Publish a dataset

Before you publish, protect the default branch of the dataset repository (`create` does this for you): require pull requests, block force pushes and deletions, and, if you want merges to wait for it, require the `verify` check. Allow auto-merge in the repository settings too. `push` then sends your commit to a branch named `incoming/<commit>` and opens a pull request, which merges automatically once the check passes. Without `gh`, `push` prints the link to open the pull request yourself. The first push to an empty repository goes straight to its default branch. `push` refuses a commit whose footprint does not hold.

`cairnml dataset doctor` checks all of this before you publish. It lists each check as `ok`, `FAIL` or `--` (nothing to check yet), with what to do for each failure:

- the repository has an origin on GitHub;
- the GitHub App is installed on the repository and its `dataset.yaml` is valid (it asks the server with your login, and shows `--` when it cannot);
- the dataset set-up, the verify workflow and the verify script are in place and current;
- `gh` is installed and logged in;
- auto-merge is allowed, and the default branch requires pull requests and blocks force pushes and deletions.

The doctor only reads these settings; it never changes them. The server's list of repositories is a snapshot rebuilt about once an hour, and the doctor says so when it is older.

## Use a dataset in a script

```python
from cairnml import Task
from cairnml.datasets import Dataset

task = Task.init(project_name="cars", task_name="train")
data = Dataset.get("https://github.com/acme/cars-data.git", "3f2a9c1")   # a commit, tag or branch
print(data.path)   # a folder with the files
```

The repository can be a URL or a local path. If it has no `dataset.yaml` at that commit, `Dataset.get` refuses it before anything is downloaded: `<repository> is not a CairnML dataset: dataset.yaml is missing at <commit>`. The task records the repository, the commit and the content hash. Pass a commit hash rather than a branch when you want to rerun the same data later.

## Use a public dataset from the registry

A public dataset has an entry in the registry: a pin file in `datasets/` that names its source and its adapter. Read it by name. An entry that names a repository of your own (`source: repo`) is refused: read that repository with `Dataset.get` instead.

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

The commit of the registry fixes both the pin file, which says where the data comes from, and the adapter, which says how it is read. Without a commit, the latest commit of its default branch is used. Pass a commit to rerun an earlier run exactly. The run records the source revision, the adapter and the registry commit. To set the registry once for all scripts, put its URL in the configuration as `sdk.datasets.external_repo`. Your own datasets are not in the registry.

### The pin file

```yaml
# datasets/coco-det.yaml
source: huggingface              # what the adapter fetches from; local means a folder used where it is
link: hf://datasets/org/coco-det
adapter: coco_det               # the adapter that reads the files, and fetches them
revision: null                  # optional: pin the source's own revision
overrides:                      # another adapter for revisions whose layout changed
  - revisions: ["9b1c4e7"]
    adapter: coco_det_v2
```

The SDK does not download anything itself: a source other than `local` is fetched by its adapter. `link` is what the adapter's `fetch` receives: an address, or a list of addresses. A web dataset can list its files with a `fetch` list instead of `link` (use one, not both), and the adapter reads that list:

```yaml
# datasets/images.yaml
source: web
adapter: images
fetch:
  - url: https://example.org/images.tar.gz
    checksum: sha256:<64 hex digits>
    unpack: tar.gz
```

A `local` source is a folder of the registry itself, or of the machine, and is read where it is.

## Write an adapter

An adapter is a small module in the registry's `adapters/` folder. It holds everything about its dataset:

- `REQUIREMENTS`: the Python packages it needs, each as an exact pin, for example `["pyarrow==23.0.1"]`. A run refuses to start if one is not installed at that version, and says the command to install it. Nothing is installed by the SDK. The cache of the built dataset is keyed by these requirements. Empty or missing means none.
- `fetch(link, revision, dest)`: downloads the source at that revision into `dest`. Required for any source other than `local`.
- `resolve(link, revision)`: returns the revision to use, for example the commit a Hub reports for a branch. Without it, a remote source must be given a revision in the pin file or the run.
- `run(source, writer)`: reads the files in `source` into the standard format.

It declares the modalities and labels it produces, and then adds one sample at a time:

```python
from cairnml.datasets import Adapter

REQUIREMENTS = []

def fetch(link, revision, dest):
    download_files(link, revision, dest)

class CocoDetection(Adapter):
    modalities = {"image": "image"}
    labels = {"bbox2d": "bbox2d"}

    def run(self, source, writer):
        for item in read_source(source):
            writer.add(item.id, item.split, {"image": item.file}, {"bbox2d": item.boxes})
```

`source` is the folder with the files of the dataset at the revision the run asked for (for a `local` source, the folder itself). Image paths are relative to that folder. The writer checks every sample against the standard format, and an error names the adapter, so the run stops before training.

## The sample format

Every adapter produces the same shape: one row per sample, with an id, a split, the files of each modality and the labels.

- Modalities are named files, for example `image` and `depth`. Types: `image`, `depth`, `audio`, `video`, `text`, `table`, `file`.
- Labels are named and typed. Types: `class`, `bbox2d`, `mask2d`, `json`, `number`, `tabular`.
- Boxes are always `[x1, y1, x2, y2]` in pixels, with `x2 > x1` and `y2 > y1`.
- `number` is one finite number. `tabular` is a dict of named columns: each value is a finite number or a string, the same column names in every sample, and at least one column. A value that is missing is written as `None`.

A table of features and a target, as in a CSV file, needs no JSON text:

```python
class Greenhouse(Adapter):
    modalities = {"table": "table"}
    labels = {"features": "tabular", "energy": "number"}

    def run(self, source, writer):
        for row in read_rows(source / "greenhouse.csv"):
            features = {"temp": row["temp"], "humidity": row["humidity"], "plant": row["plant"]}
            writer.add(row["id"], row["split"], {"table": row["file"]}, {"features": features, "energy": row["energy"]})
```

A tabular label is stored in `samples.parquet` as a list of cells, one per column, each with `column`, `number` and `text` (one of the last two is set). Numbers come back as floats. `sample.label("features")` returns the dict again.

## The cache

Datasets are downloaded once per machine into a per-user cache, by default `~/.cairnml/cache/datasets`, up to 50 GB. When the cache is over its limit, the versions used longest ago are removed first. A version in use is never removed.

```bash
cairnml dataset cache info
cairnml dataset cache limit 20GB
cairnml dataset cache clean
```

On an agent, mount the cache into the job containers, so it survives between jobs.

## Access

GitHub is reached through a GitHub App only. The organisation that runs CairnML generates its own App once; it is not shared between organisations. A private repository is read with the App that is attached to it (the company's, or your own), and the App must be installed on every repository that jobs or agents read, including each dataset repository.

- A **public repository** is read over https with no token: no login and no GitHub App. An attached App only lifts the GitHub rate limit.
- A **private repository** needs the GitHub App. Outside a job, your own git login is used; inside a job, the agent gets a read-only token that the server makes with the credential the dataset holds, not with any access shared with the project.
- A dataset in **LFS** needs only git and git-lfs, so a public one needs no login at all.
- A dataset in **S3** gets short-lived storage keys from the server, so no access key is stored on your machine. Reading it needs a CairnML login, even when its repository is public.

**Discovery:** your own datasets are found through the GitHub App installation. A repository the App is installed on, with a `dataset.yaml` at its default branch, is a dataset you can read with `Dataset.get(<repository>, <commit>)`. A repository the server does not list is not discovered: check that the App is installed on it and that its default branch has a `dataset.yaml`. `cairnml dataset doctor` runs this check when it can reach the server.

## Having problems

Check the [open issues](https://github.com/gianlucapargaetzi/cairnml/issues), or [open a new issue](https://github.com/gianlucapargaetzi/cairnml/issues/new) with the commands you ran and the full error message.
