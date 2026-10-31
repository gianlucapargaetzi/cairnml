import argparse
import sys
from argparse import ArgumentParser
from typing import List, Optional

from cairnml.datasets import DatasetCache, DatasetError, DatasetRepo, Dataset
from cairnml.datasets.cache import parse_size
from cairnml.datasets.repo import REQUIRED_CHECK


def _print_group(title: str, changes: dict) -> None:
    rows = [(label, p) for kind, label in (("added", "new file"), ("modified", "modified"), ("deleted", "deleted"))
            for p in changes.get(kind, [])]
    if not rows:
        return
    print(f"{title}\n")
    for label, path in rows:
        print(f"    {label + ':':<10} {path}")
    print()


def do_init(args: argparse.Namespace) -> None:
    url = DatasetRepo(args.path).init(
        remote_url=args.remote, adapter=args.adapter, registry=args.registry, storage=args.storage
    )
    how = f"through the adapter {args.adapter!r}" if args.adapter else "as they are (native format)"
    print(f"Dataset repository set up. Files are stored in {url}; data/ is read {how} (see dataset.yaml).")
    print("Copy your files into data/, then run: cairnml dataset status")


def do_create(args: argparse.Namespace) -> None:
    result = DatasetRepo.create(
        args.path or args.name, args.name, args.source, private=not args.public, storage=args.storage,
        adapter=args.adapter, registry=args.registry, protect=not args.no_protect,
    )
    print(f"Created {result['url']} with the dataset in {result['where']}, committed {result['commit'][:10]} and "
          f"pushed to {result['branch']}.")
    protection = result["protection"]
    if protection is None:
        print("Branch protection skipped (--no-protect). Protect the default branch by hand: "
              "cairnml dataset doctor shows what is missing.")
    elif protection["ok"]:
        check = (f"the {REQUIRED_CHECK} check is required" if protection["check"]
                 else f"no {REQUIRED_CHECK} check is required yet (its first run had not reported); "
                      "run cairnml dataset doctor after it has")
        print(f"Protected {result['branch']}: pull requests required, force pushes and deletions blocked, {check}.")
    else:
        print(f"Warning: {result['branch']} is NOT protected: {protection['problem']}.\n"
              "Protect it by hand (pull requests, no force pushes or deletions, the verify check), "
              "or run cairnml dataset create again with administration rights; cairnml dataset doctor shows what is missing.")


def do_status(args: argparse.Namespace) -> None:
    status = DatasetRepo(args.path).status()
    head = f" (last commit {status['commit'][:10]})" if status["commit"] else " (no commits yet)"
    print(f"On branch {status['branch']}{head}\n")
    _print_group("Added, ready to commit:", status["added"])
    _print_group("Changed, not added (run: cairnml dataset add):", status["not_added"])
    if status["untracked"]:
        print("Not tracked yet (run: cairnml dataset add):\n")
        for path in status["untracked"]:
            print(f"    {path}")
        print()
    if status["other"]:
        print("Other files changed:\n")
        for path in status["other"]:
            print(f"    {path}")
        print()
    if not (status["added"] and any(status["added"].values())) and not any(status["not_added"].values()) \
            and not status["untracked"]:
        print("The dataset is clean.")


def do_add(args: argparse.Namespace) -> None:
    status = DatasetRepo(args.path).add()
    counts = {kind: len(files) for kind, files in status["added"].items()}
    print(f"Added: {counts['added']} new, {counts['modified']} modified, {counts['deleted']} deleted. "
          f"Run: cairnml dataset commit -m \"message\"")


def do_check(args: argparse.Namespace) -> None:
    lock = DatasetRepo(args.path).check()
    fmt = lock["format"]
    how = "native" if fmt["kind"] == "native" else f"adapter {fmt['name']}"
    print(f"The data passes ({how}): {lock['samples']} samples, {lock['dvc']['nfiles']} files. "
          f"dataset.lock.json is written and staged.")


def do_commit(args: argparse.Namespace) -> None:
    print(f"Committed {DatasetRepo(args.path).commit(args.message)} (the data was checked)")


def do_push(args: argparse.Namespace) -> None:
    repo = DatasetRepo(args.path)
    result = repo.push()
    pushed = f"Pushed {result['commit'][:10]} to {result['branch']}"
    if result["pull_request"] and result["auto_merge"]:
        print(f"{pushed}; pull request with auto-merge: {result['pull_request']}")
    elif result["pull_request"]:
        print(f"{pushed}; pull request: {result['pull_request']}")
        print(f"Auto-merge could not be switched on ({result['problem']}). Merge it by hand once its check passes.")
    elif result["branch"].startswith("incoming/"):
        print(f"{pushed}. No pull request was opened ({result['problem']}). "
              f"Open one into the default branch: {repo.compare_url(result['branch'])}")
    else:
        print(pushed)


def do_doctor(args: argparse.Namespace) -> None:
    checks = DatasetRepo(args.path).doctor()
    labels = {True: "ok  ", False: "FAIL", None: "--  "}
    for item in checks:
        print(f"{labels[item['ok']]} {item['check']}: {item['message']}")
    failed = [item for item in checks if item["ok"] is False]
    if failed:
        raise DatasetError(f"{len(failed)} check(s) failed: fix them, then run cairnml dataset doctor again")
    print("\nNo problems found." if all(item["ok"] for item in checks) else "\nNothing failed.")


def do_register(args: argparse.Namespace) -> None:
    result = DatasetRepo(args.path).register(name=args.name)
    print(f"Registered {result.get('name')}: {result.get('pull_request') or 'entry added'}")


def do_get(args: argparse.Namespace) -> None:
    dataset = Dataset.get(args.repo, args.commit)
    print(dataset.path)


def do_cache(args: argparse.Namespace) -> None:
    cache = DatasetCache.default()
    if args.action == "info":
        print(f"{cache.root}: {cache.usage() / 1024**3:.2f} GB used, limit {cache.max_bytes / 1024**3:.2f} GB, "
              f"{len(cache.checkouts())} dataset versions")
    elif args.action == "clean":
        print(f"Removed {cache.clear()} dataset versions")
    else:
        cache.max_bytes = parse_size(args.limit)
        print(f"Removed {cache.evict()} dataset versions to fit {args.limit}")


def cli(argv: Optional[List[str]] = None) -> int:
    title = "cairnml dataset - versioned datasets on git and DVC"
    parser = ArgumentParser(prog="cairnml dataset", description=title)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, function, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=function)
        p.add_argument("--path", default=".", help="the dataset repository (default: the current folder)")
        return p

    init = command("init", do_init, "set up git tracking and file storage in a cloned repository")
    init.add_argument("--remote", help="store the files here instead of in the server's dataset bucket")
    init.add_argument("--adapter", help="read data/ through this adapter of the registry (default: data/ is native)")
    init.add_argument("--registry", help="URL of the adapter registry (default: sdk.datasets.external_repo)")
    init.add_argument("--storage", choices=["s3", "lfs"], default="s3",
                      help="s3: files in the server's dataset bucket through DVC; lfs: files in the GitHub LFS of "
                           "the repository (no DVC, no storage keys). Chosen once; the default is s3")
    command("status", do_status, "show the branch and what changed in data/")
    command("add", do_add, "record the current state of data/, including deleted files")
    command("check", do_check, "check the data in its declared format and write the footprint")
    commit = command("commit", do_commit, "check the data, then commit it with its footprint")
    commit.add_argument("-m", "--message", required=True)
    command("doctor", do_doctor, "check the set-up and the GitHub settings that publishing relies on")
    command("push", do_push, "upload the files, then publish the commit (through a pull request on the default branch)")
    register = command("register", do_register, "add the dataset to the registry (once; the server checks it)")
    register.add_argument("--name", help="registry name (default: the repository name)")
    get = command("get", do_get, "download a dataset version into the cache and print its folder")
    get.add_argument("repo", help="URL of the dataset repository")
    get.add_argument("commit", help="commit, branch or tag")
    create = sub.add_parser(
        "create", help="create the GitHub repository, copy the files in, check, commit and push, in one call"
    )
    create.set_defaults(func=do_create)
    create.add_argument("name", help="name of the new GitHub repository")
    create.add_argument("--from", dest="source", required=True, help="folder that holds the dataset files")
    create.add_argument("--path", help="local folder for the repository (default: the name, in this folder)")
    create.add_argument("--public", action="store_true", help="make the repository public (default: private)")
    create.add_argument("--storage", choices=["s3", "lfs"], default="s3", help="where the files are stored (see init)")
    create.add_argument("--adapter", help="read data/ through this adapter of the registry (default: data/ is native)")
    create.add_argument("--registry", help="URL of the adapter registry (default: sdk.datasets.external_repo)")
    create.add_argument("--no-protect", action="store_true",
                        help="do not protect the default branch (needs administration rights on the repository)")
    cache = sub.add_parser("cache", help="show or trim the local dataset cache")
    cache.set_defaults(func=do_cache)
    cache.add_argument("action", choices=["info", "clean", "limit"])
    cache.add_argument("limit", nargs="?", default="50GB", help="size, for the limit action (for example 20GB)")

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    sys.exit(cli())


if __name__ == "__main__":
    main()
