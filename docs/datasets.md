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

A dataset of your own is a git repository that works with DVC. The two tools hold different things:

- **Git** holds the metadata: `data.dvc` lists the files in `data/` by their content hash, `dataset.yaml` says how the
  files are read, and `dataset.lock.json` is the footprint, the record that the data was checked. The verify script and
  its workflow are in git too. Git holds no data files, so the repository stays small.
- **DVC** holds the files themselves, in the dataset storage behind the CairnML server. `push` uploads the files before
  it publishes the commit, so a commit on GitHub never refers to files that are not stored.

What follows from this:

- **The commit is the version.** A run records the repository, the commit and the content hash of the data it used, so
  the run always shows which data it used. Pass a commit when you want to rerun the same data later.
- **Old versions keep their files.** Removing a file makes a new commit; the earlier commit still refers to the old file.
- **The checks need no data.** The footprint says what the data is, so the verify check in the repository reads git only.
- **The default branch changes only through pull requests** that pass the verify check. `push` takes care of the branch
  and the pull request, given the settings `cairnml dataset doctor` checks.
- **Nothing secret is stored on your machine.** DVC gets short-lived keys from the server when it needs them.

## Create a dataset of your own

Create an empty repository on GitHub and clone it. Then, inside the clone:

```bash
cairnml dataset init                     # git tracking, file storage and dataset.yaml
cp -r /path/to/measurements data/        # copy your files into data/
cairnml dataset status                   # what is new, changed or deleted
cairnml dataset add                      # record data/ as it is now
cairnml dataset commit -m "March measurements"   # check the data and commit it with its footprint
cairnml dataset doctor                   # once: check the set-up and the GitHub settings
cairnml dataset push                     # send the files, then the commit, as a pull request
cairnml dataset register                 # once: add the dataset to the registry
```

`add` records the whole of `data/`: new and changed files are included, and files you removed are removed from the dataset. Older commits keep their files, so an earlier version stays available.

## How the data is read

Every dataset says how its files are read in `dataset.yaml`, which holds one of two lines:

- `format: native`: `data/` already contains the standard sample table, `samples.parquet` with `manifest.json`.
- `adapter: <name>`, with `registry: <url>`: an adapter from the registry reads the files in `data/` into the standard format. `cairnml dataset init --adapter <name> --registry <url>` writes this line for you.

`cairnml dataset check` reads `data/` the way `dataset.yaml` says and reports what is wrong. A dataset that is neither native nor read by an adapter cannot be committed.

## Publish a dataset

Protect the default branch of the dataset repository: require pull requests, block force pushes, and require the `verify` check. Allow auto-merge in the repository settings too. `push` then sends your commit to a branch named `incoming/<commit>` and opens a pull request, which merges automatically once the check passes. Without the GitHub command line (`gh`), `push` prints the link to open the pull request yourself. The first push to an empty repository goes straight to its default branch.

`cairnml dataset doctor` checks this before you publish. It lists each check as `ok`, `FAIL` or `--` (nothing to check yet), with what to do for each failure:

- the repository has an origin on GitHub, and the dataset set-up, `dataset.yaml`, the verify workflow and the verify script are in place and current;
- the GitHub command line (`gh`) is installed and logged in;
- auto-merge is allowed in the repository settings;
- the default branch requires pull requests, blocks force pushes and requires the `verify` check.

The doctor only reads these settings; it never changes them. Set them on the repository's settings page, which the doctor links to.

`push` refuses a commit whose footprint does not hold.

## Register a dataset

`cairnml dataset register` asks the server to add the dataset to the registry. The server reads the default branch of the repository, verifies the footprint, and opens a pull request in the registry with the new entry. Registration happens once. A repository that is already registered, or waiting in a pull request, is refused. Its commit must already be on the default branch.

## Use a dataset in a script

```python
from cairnml import Task
from cairnml.datasets import Dataset

task = Task.init(project_name="cars", task_name="train")
data = Dataset.get("https://github.com/acme/cars-data.git", "3f2a9c1")   # a commit, tag or branch
print(data.path)   # a folder with the files
```

The files are downloaded once into your cache and reused by every later run on the machine. The task records the repository, the commit and the content hash, so the run always shows which data it used. Pass a commit hash rather than a branch when you want to rerun the same data later.

## Use a public dataset from the registry

A public dataset has an entry in the registry: a pin file in `datasets/` that names its source and its adapter. Read it by name:

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
source: huggingface              # or git, local, or web
link: hf://datasets/org/coco-det
adapter: coco_det               # the adapter that reads the files
revision: null                  # optional: pin the source's own revision
overrides:                      # another adapter for revisions whose layout changed
  - revisions: ["9b1c4e7"]
    adapter: coco_det_v2
```

A `web` source takes a list of addresses under `link`. A zip archive among them is unpacked into a folder named after it.

A web dataset can describe its files in a `fetch` list instead of `link`. Each entry gives the address of one file, the checksum the publisher gives for it, and how to unpack it:

```yaml
# datasets/images.yaml
source: web
adapter: images
fetch:
  - url: https://example.org/images.tar.gz
    checksum: sha256:<64 hex digits>   # optional: md5:<32 hex digits> or sha256:<64 hex digits>
    unpack: tar.gz                     # optional: none, zip, tar.gz or gz
  - url: https://example.org/labels.gz
    checksum: md5:<32 hex digits>
    unpack: gz
```

- `url` is an `https://`, `http://` or `file://` address. Use `fetch` or `link`, not both.
- `checksum` is optional. When it is given, the file is verified after the download, and a file that does not match is refused: the run fails with a message that names the file. When every file has a checksum, the revision of the dataset is a hash of the checksums, and the server is not asked for anything. A file without a checksum is identified by the server's ETag and size instead, and is not verified.
- `unpack` defaults to `zip` for a file that ends in `.zip`, and to `none` otherwise. `tar.gz` and `zip` unpack into a folder named after the archive (`images.tar.gz` becomes `images/`). `gz` decompresses one file, written next to the archive under its name without `.gz`. The archive is removed after it is unpacked.
- The pin file is checked when it is read. A wrong checksum, an unknown key or an unknown `unpack` is reported with the file name before anything is downloaded.

Each file is downloaded once into the cache, as for every source. The run records the revision and the adapter, as it does for any dataset from the registry.

## Write an adapter

An adapter is a small class in the registry's `adapters/` folder. It declares the modalities and labels it produces, and then adds one sample at a time:

```python
from cairnml.datasets import Adapter

class CocoDetection(Adapter):
    modalities = {"image": "image"}
    labels = {"bbox2d": "bbox2d"}

    def run(self, source, writer):
        for item in read_source(source):
            writer.add(item.id, item.split, {"image": item.file}, {"bbox2d": item.boxes})
```

`source` is the folder with the files of the dataset at the revision the run asked for. Image paths are relative to that folder. The writer checks every sample against the standard format, and an error names the adapter, so the run stops before training.

## The sample format

Every adapter produces the same shape: one row per sample, with an id, a split, the files of each modality and the labels.

- Modalities are named files, for example `image` and `depth`. Types: `image`, `depth`, `audio`, `video`, `text`, `table`, `file`.
- Labels are named and typed. Types: `class`, `bbox2d`, `mask2d`, `json`.
- Boxes are always `[x1, y1, x2, y2]` in pixels, with `x2 > x1` and `y2 > y1`.

## The cache

Datasets are kept per user, by default in `~/.cairnml/cache/datasets`, up to 50 GB. When the cache is over its limit, the versions used longest ago are removed first. A version in use is never removed.

```bash
cairnml dataset cache info
cairnml dataset cache limit 20GB
cairnml dataset cache clean
```

On an agent, mount the cache into the job containers, so it survives between jobs.

## Access

DVC gets short-lived keys for the dataset storage from the server, so no access key is stored on your machine. Git uses your own login. In a job, the agent gets a read-only token for the registry and for the dataset repositories its entries point to, and for no other repository.

## Having problems

1. Check the [open issues](https://github.com/gianlucapargaetzi/cairnml/issues).
2. If your problem is not listed, [open a new issue](https://github.com/gianlucapargaetzi/cairnml/issues/new) with the commands you ran and the full error message.
