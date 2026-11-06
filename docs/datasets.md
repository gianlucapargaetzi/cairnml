# Datasets

CairnML versions the datasets your runs use. A dataset of your own is a git repository whose files are tracked with DVC, so every run records the exact commit and content it used. Public datasets are read through adapters from the [dataset registry](https://github.com/gianlucapargaetzi/cairnml-dataset-registry), which turns each source into the same sample format.

## Features

- Versioned datasets: the commit of the repository is the version of the data.
- A footprint check on every commit, and a continuous-integration check that repeats it without the data.
- Public datasets read through adapters, with the source revision recorded on the run.
- One sample format for every dataset, with named modalities and typed labels.
- A local cache, so a dataset is downloaded once per machine.

## Prerequisites

- Python 3.9 or newer
- git
- A running [CairnML server](https://github.com/gianlucapargaetzi/cairnml-server), for storing the files and recording the runs

## Installation

```bash
pip install "cairnml[dataset]"
```

## How a dataset is stored

A dataset of your own is a git repository. Git holds the metadata and the files are held in one of two places, chosen
once when the dataset is created and recorded in `dataset.yaml` (`storage: s3` or `storage: lfs`):

- **Git** holds the metadata: `dataset.yaml` says how the files are read and where they are stored, and
  `dataset.lock.json` is the footprint, the record that the data was checked. The verify script and its workflow are in
  git too. With `storage: s3` git also holds `data.dvc`, which lists the files in `data/` by their content hash.
- **The files** are held in one of two places:
  - `storage: s3` (the default): DVC holds the files in the dataset storage behind the CairnML server. `push` uploads the
    files before it publishes the commit, so a commit on GitHub never refers to files that are not stored.
  - `storage: lfs`: the files are held in the GitHub LFS of the dataset repository (`.gitattributes` keeps `data/` in
    LFS). No DVC and no storage keys are involved: a clone needs git and git-lfs only. `push` uploads the files to the
    LFS of the repository before it publishes the commit. The footprint records the LFS object of every file.

Choose `--storage lfs` when the repository can hold the files (GitHub LFS has its own storage and bandwidth limits);
choose `s3` for data that should live in the server's bucket. Either way the storage cannot be changed afterwards.

What follows from this:

- **The commit is the version.** A run records the repository, the commit and the content hash of the data it used, so
  the run always shows which data it used. Pass a commit when you want to rerun the same data later.
- **Old versions keep their files.** Removing a file makes a new commit; the earlier commit still refers to the old file.
- **Fetching by hand.** A dataset in LFS is fetched with `git clone <repository>`, `git checkout <commit>` and
  `git lfs pull`. A dataset in S3 is fetched with `cairnml dataset get <repository> <commit>`, which holds the keys.
- **The checks need no data.** The footprint says what the data is, so the verify check in the repository reads git only.
- **The default branch changes only through pull requests** that pass the verify check. `push` takes care of the branch
  and the pull request, given the settings `cairnml dataset doctor` checks.
- **Nothing secret is stored on your machine.** DVC gets short-lived keys from the server when it needs them.

A repository is a dataset when its root holds `dataset.yaml` at the commit you read. `dataset.lock.json` (and `data.dvc`
for `s3`) is the footprint that records the check. `DATASET.md` is optional: the web app shows it as the dataset's page.
The SDK never requires it and never reads it to read data, and `cairnml dataset` does not create it.

## Create a dataset of your own

The shortest way is one command. It creates a private repository on GitHub with the GitHub command line (`gh`, logged in
with `gh auth login`), copies the files in, checks them, commits and pushes:

```bash
cairnml dataset create cars-data --from /path/to/measurements --storage lfs
cairnml dataset doctor                   # once: check the GitHub settings
```

`--public` makes the repository public, `--path` chooses the local folder (default: the name), and `--adapter` and
`--registry` say how the data is read (see below). With `--adapter local`, the `adapter.py` in the `--from` folder is the
adapter: it goes to the top of the repository, and the other files go to `data/`. The command stops with a message if the
check fails; fix the data and run `cairnml dataset commit` and `cairnml dataset push` in that folder.

Right after the first push, `create` protects the default branch of the new repository: pull requests are required
(no approvals needed, and administrators are not held to them), and force pushes and deletions are blocked. No status
check is required by this step; to make merges wait for the `verify` check, add it as a required check on the
repository's settings page. Setting this up needs
**administration rights on the repository**: the Administration permission for a token, or the admin role for your
login. Without them, `create` still makes and publishes the repository, and prints a warning that the branch is not
protected and which permission is missing. Pass `--no-protect` to skip the step and protect the branch yourself, as
`cairnml dataset doctor` describes.

To use a repository you have already created on GitHub, clone it. Then, inside the clone:

```bash
cairnml dataset init --storage lfs       # git tracking, the storage and dataset.yaml (s3 is the default)
cp -r /path/to/measurements data/        # copy your files into data/
cairnml dataset status                   # what is new, changed or deleted
cairnml dataset add                      # record data/ as it is now
cairnml dataset commit -m "March measurements"   # check the data and commit it with its footprint
cairnml dataset doctor                   # once: check the set-up and the GitHub settings
cairnml dataset push                     # send the files, then the commit, as a pull request
```

`add` records the whole of `data/`: new and changed files are included, and files you removed are removed from the dataset. Older commits keep their files, so an earlier version stays available.

## How the data is read

Every dataset says how its files are read in `dataset.yaml`, which holds one of three forms:

- `format: native`: `data/` already contains the standard sample table, `samples.parquet` with `manifest.json`.
- `adapter: <name>`, with `registry: <url>`: an adapter from the registry reads the files in `data/` into the standard format. `cairnml dataset init --adapter <name> --registry <url>` writes this line for you.
- `adapter: local`: the adapter is `adapter.py` at the top of the repository, next to `dataset.yaml`, and no registry is needed. `cairnml dataset init --adapter local` writes this line for you.

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

The adapter runs only when you check the data on your machine (`cairnml dataset check`, `commit` and `create`). The footprint
records the sha256 of `adapter.py`, so a change to the adapter after the check is found by `cairnml dataset push` and by the
repository's CI check, which reads the hash and does not run the adapter. The data must then be checked again. Reading a dataset with
`Dataset.get` does not run the adapter, and a registry entry never reads a repository of your own, because the registry
would otherwise run code from the repository.

## Publish a dataset

The organisation that runs CairnML generates its own GitHub App, once. It is not shared between organisations: every repository that a job or an agent reads needs the App installed, and that includes each dataset repository. Install it on a dataset repository before you publish to it; `cairnml dataset doctor` tells you when it is missing.

Protect the default branch of the dataset repository: require pull requests, block force pushes and deletions, and, if you want merges to wait for it, require the `verify` check. Allow auto-merge in the repository settings too. `push` then sends your commit to a branch named `incoming/<commit>` and opens a pull request, which merges automatically once the check passes. Without the GitHub command line (`gh`), `push` prints the link to open the pull request yourself. The first push to an empty repository goes straight to its default branch.

`cairnml dataset doctor` checks this before you publish. It lists each check as `ok`, `FAIL` or `--` (nothing to check yet), with what to do for each failure:

- the repository has an origin on GitHub;
- the GitHub App is installed on the repository and its `dataset.yaml` is valid. The doctor asks the server, with your CairnML login, whether it lists the repository. It shows `--` when the server cannot be reached or you are not logged in. Otherwise a `FAIL` means the App is not installed on the repository, or the repository has no `dataset.yaml` yet. The server's list is a snapshot rebuilt about once an hour; the doctor says so when it is older, and does not refresh it;
- the dataset set-up, `dataset.yaml`, the verify workflow and the verify script are in place and current;
- the GitHub command line (`gh`) is installed and logged in;
- auto-merge is allowed in the repository settings;
- the default branch requires pull requests and blocks force pushes and deletions (`create` sets this up, see above).

The doctor only reads these settings; it never changes them. Set them on the repository's settings page, which the doctor links to.

`push` refuses a commit whose footprint does not hold.

## Discovery and access

Your own datasets are not in the registry. They are discovered through the GitHub App installation: a repository the
GitHub App is installed on, with a `dataset.yaml`, is a dataset you can read with `Dataset.get(<repository>, <commit>)`. The GitHub App is needed
for private repositories and for discovery. A public repository needs no login and no GitHub App.

The organisation that runs CairnML generates its own GitHub App once; it is not shared between organisations. The App must be installed on every repository that jobs or agents read, including each dataset repository. A repository that is not listed by the server is not discovered: check that the App is installed on it and that its default branch has a `dataset.yaml`. `cairnml dataset doctor` runs this check when it can reach the server.

The [dataset registry](https://github.com/gianlucapargaetzi/cairnml-dataset-registry) holds public datasets and adapters
only. Nothing is added to it for your own datasets.

## Use a dataset in a script

```python
from cairnml import Task
from cairnml.datasets import Dataset

task = Task.init(project_name="cars", task_name="train")
data = Dataset.get("https://github.com/acme/cars-data.git", "3f2a9c1")   # a commit, tag or branch
print(data.path)   # a folder with the files
```

The repository can be a URL or a local path. If it has no `dataset.yaml` at that commit, `Dataset.get` refuses it before
anything is downloaded: `<repository> is not a CairnML dataset: dataset.yaml is missing at <commit>`.

The files are downloaded once into your cache and reused by every later run on the machine. The task records the repository, the commit and the content hash, so the run always shows which data it used. Pass a commit hash rather than a branch when you want to rerun the same data later.

## Use a public dataset from the registry

A public dataset has an entry in the registry: a pin file in `datasets/` that names its source and its adapter. Read it by name.
An entry that names a repository of your own (`source: repo`) is refused: read that repository with `Dataset.get` instead.

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

The commit of the registry fixes both the pin file, which says where the data comes from, and the adapter, which says how it is read. Without a commit, the latest commit of its default branch is used. Pass a commit to rerun an earlier run exactly. The run records the source revision, the adapter and the registry commit. To set the registry once for all scripts, put its URL in the configuration as `sdk.datasets.external_repo`.

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

The SDK does not download anything itself. A source other than `local` is fetched by its adapter, so the adapter says how its
source is reached. `link` is what the adapter's `fetch` receives: an address, or a list of addresses. A web dataset can list
its files with a `fetch` list instead of `link`, and the adapter reads that list:

```yaml
# datasets/images.yaml
source: web
adapter: images
fetch:
  - url: https://example.org/images.tar.gz
    checksum: sha256:<64 hex digits>
    unpack: tar.gz
```

Use `fetch` or `link`, not both. A `local` source is a folder of the registry itself, or of the machine, and is read where it is.

Each source is downloaded once into the cache, as for every dataset. The run records the revision and the adapter, as it does for any dataset from the registry.

## Write an adapter

An adapter is a small module in the registry's `adapters/` folder. It holds everything about its dataset:

- `REQUIREMENTS`: the Python packages it needs, each as an exact pin, for example `["pyarrow==23.0.1"]`. A run refuses to start
  if one is not installed at that version, and says the command to install it (`pip install "pyarrow==23.0.1"`). Nothing is
  installed by the SDK. The cache of the built dataset is keyed by these requirements. Empty or missing means none.
- `fetch(link, revision, dest)`: downloads the source at that revision into `dest`. Required for any source other than `local`.
- `resolve(link, revision)`: returns the revision to use, for example the commit a Hub reports for a branch. Without it, a
  remote source must be given a revision in the pin file or the run.
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
- `number` is one finite number. `tabular` is a dict of named columns: each value is a finite number or a string, the
  same column names in every sample, and at least one column. A value that is missing is written as `None`.

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

A tabular label is stored in `samples.parquet` as a list of cells, one per column, each with `column`, `number` and `text`
(one of the last two is set). Numbers come back as floats. `sample.label("features")` returns the dict again.

## The cache

Datasets are kept per user, by default in `~/.cairnml/cache/datasets`, up to 50 GB. When the cache is over its limit, the versions used longest ago are removed first. A version in use is never removed.

```bash
cairnml dataset cache info
cairnml dataset cache limit 20GB
cairnml dataset cache clean
```

On an agent, mount the cache into the job containers, so it survives between jobs.

## Access

DVC gets short-lived keys for the dataset storage from the server, so no access key is stored on your machine. Git uses
your own login outside a job. In a job, the agent gets a read-only token for any repository the GitHub App is installed on.

- A **public repository** is read over https with no token: no login and no GitHub App.
- A **private repository** needs the GitHub App. Outside a job, your own git login is used; inside a job, the token the server issues.
- A dataset in **LFS** needs only git and git-lfs, so a public one needs no login at all.
- A dataset in **S3** gets its storage keys from the server, so reading it needs a CairnML login, even when its repository is public.

## Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml/issues/new) with the commands you ran and the full error message.
