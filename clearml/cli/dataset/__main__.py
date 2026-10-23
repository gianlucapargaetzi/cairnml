import argparse
import sys
from argparse import ArgumentParser
from typing import List, Optional

from clearml.datasets import DatasetCache, DatasetError, DatasetRepo, Dataset
from clearml.datasets.cache import parse_size


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
    url = DatasetRepo(args.path).init(remote_url=args.remote)
    print(f"Dataset repository set up. Files are stored at {url}.")
    print("Copy your files into data/, then run: clearml-dataset status")


def do_status(args: argparse.Namespace) -> None:
    status = DatasetRepo(args.path).status()
    head = f" (last commit {status['commit'][:10]})" if status["commit"] else " (no commits yet)"
    print(f"On branch {status['branch']}{head}\n")
    _print_group("Added, ready to commit:", status["added"])
    _print_group("Changed, not added (run: clearml-dataset add):", status["not_added"])
    if status["untracked"]:
        print("Not tracked yet (run: clearml-dataset add):\n")
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
          f"Run: clearml-dataset commit -m \"message\"")


def do_commit(args: argparse.Namespace) -> None:
    print(f"Committed {DatasetRepo(args.path).commit(args.message)}")


def do_push(args: argparse.Namespace) -> None:
    print(f"Pushed {DatasetRepo(args.path).push()}")


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
    title = "clearml-dataset - versioned datasets on git and DVC"
    parser = ArgumentParser(prog="clearml-dataset", description=title)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, function, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=function)
        p.add_argument("--path", default=".", help="the dataset repository (default: the current folder)")
        return p

    init = command("init", do_init, "set up git tracking and file storage in a cloned repository")
    init.add_argument("--remote", help="store the files here instead of in the server's dataset bucket")
    command("status", do_status, "show the branch and what changed in data/")
    command("add", do_add, "record the current state of data/, including deleted files")
    commit = command("commit", do_commit, "commit the dataset and its references")
    commit.add_argument("-m", "--message", required=True)
    command("push", do_push, "upload the files, then the commits")
    get = command("get", do_get, "download a dataset version into the cache and print its folder")
    get.add_argument("repo", help="URL of the dataset repository")
    get.add_argument("commit", help="commit, branch or tag")
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
