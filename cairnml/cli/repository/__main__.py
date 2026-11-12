"""cairnml repository: the code repositories, datasets and registries of datasets, as the web app lists them under Repositories and
Datasets.

    cairnml repository list [--type code|dataset|registry] [--json]
    cairnml repository add <url> [--type code|dataset|registry] [--project P] [--credential C | --none-needed] [--shared]
    cairnml repository assign <repository> <credential>     your own credential, or a company credential
    cairnml repository assign <repository> --none-needed    the host clones it without a credential
    cairnml repository unassign <repository>                clear your own assignment
    cairnml repository check <repository>                   look at it again
    cairnml repository delete <repository>                  remove it (its owner and the admin)
    cairnml repository link <repository> <project>          list it in a project (and unlink)
    cairnml repository share <repository> --credential C    make it shared: owned by the admin, visible to everyone, read with
                                                            the company credential C (or --none-needed when public); and unshare

A repository is named by its address, its name or its id. A credential is yours (``cairnml credential list`` shows the ids) and is
never shared with a project: it reads for you, and for a project when you own it. A shared repository is read with a company
credential only.
"""

import json
import sys
from argparse import ArgumentParser
from typing import List, Optional

from ...datasets import DatasetError
from ...datasets import credentials
from .._table import table

READ_AS = {
    "assigned": "credential",
    "none_needed": "none needed",
    "anonymous": "public",
    "company": "company app",
    "unknown": "unknown",
    "unreachable": "not readable",
}
TYPES = ("code", "dataset", "registry")


def _lines(rows: list) -> list:
    """(repository, type, owner, read as, your credential, message) for each repository."""
    lines = []
    for row in rows:
        mine = next((a for a in row.get("assignments") or [] if a.get("mine")), None)
        access = row.get("access") or {}
        lines.append((
            f"{row.get('host', '')}/{row.get('path', '')}".strip("/"),
            ", ".join(row.get("roles") or []) or "-",
            "shared" if row.get("shared") else (row.get("owner_name") or ""),
            READ_AS.get(access.get("state"), access.get("state") or ""),
            (mine or {}).get("credential_name") or "",
            access.get("message") or "",
        ))
    return lines


def list_main(role: Optional[str] = None, as_json: bool = False) -> int:
    """Lists the repositories you see, all or only those used as ``role``."""
    try:
        rows = credentials.fetch_repository_list()
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if role:
        rows = [row for row in rows if role in (row.get("roles") or [])]
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No repositories yet. A repository is listed once a run in one of your projects uses it, or with cairnml repository add.")
        return 0
    lines = _lines(rows)
    print(table(("REPOSITORY", "TYPE", "OWNER", "READ AS", "YOUR CREDENTIAL"), [line[:5] for line in lines]))
    for repository, _type, _owner, state, _credential, message in lines:
        if state in ("not readable", "unknown"):
            print(f"\n{repository}: {message}")
    return 0


def _show(row: dict) -> None:
    access = row.get("access") or {}
    print(f"{row.get('host', '')}/{row.get('path', '')}: {READ_AS.get(access.get('state'), access.get('state'))}"
          f" ({access.get('message') or 'ok'})")


def _run(action) -> int:
    try:
        action()
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = ArgumentParser(prog="cairnml repository", description="code repositories, datasets and registries")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list the repositories you see, and how each is read")
    listing.add_argument("--type", choices=TYPES, help="only the repositories used as this")
    listing.add_argument("--json", action="store_true", help="print the list as the server returns it")

    add = sub.add_parser("add", help="list a repository before a run uses it")
    add.add_argument("url", help="the address of the repository, for example https://github.com/owner/name")
    add.add_argument("--type", choices=TYPES, default="dataset", help="how it is used (default: dataset)")
    add.add_argument("--project", help="list it in this project too")
    add.add_argument("--credential", help="your own credential, or a company credential, that reads it (cairnml credential list)")
    add.add_argument("--none-needed", action="store_true", help="the host clones it without a credential")
    add.add_argument("--shared", action="store_true", help="shared: owned by the admin and visible to everyone (the admin only)")

    assign = sub.add_parser("assign", help="set your own credential for a repository")
    assign.add_argument("repository", help="its address, its name or its id")
    assign.add_argument("credential", nargs="?", help="the credential id; with none, use --none-needed, or --clear")
    assign.add_argument("--none-needed", action="store_true", help="the host clones it without a credential")
    assign.add_argument("--type", choices=TYPES, default="dataset", help="how it is used, when it is not listed yet")

    for name, text in (("unassign", "clear your own credential for a repository"), ("check", "look at a repository again"),
                       ("delete", "remove a repository, with its assignments and links (its owner and the admin)"),
                       ("unshare", "make a shared repository non-shared again (the admin)")):
        sub.add_parser(name, help=text).add_argument("repository", help="its address, its name or its id")
    share = sub.add_parser("share", help="make a repository shared (its owner or the admin; ownership moves to the admin)")
    share.add_argument("repository", help="its address, its name or its id")
    share.add_argument("--credential", help="the company credential that reads it (confirmed before it is shared)")
    share.add_argument("--none-needed", action="store_true", help="it is public: the host clones it without a credential")
    for name, text in (("link", "list a repository in a project"), ("unlink", "take a repository out of a project's list")):
        parser_ = sub.add_parser(name, help=text)
        parser_.add_argument("repository", help="its address, its name or its id")
        parser_.add_argument("project", help="the project id")

    args = parser.parse_args(argv)
    command = args.command
    if command == "list":
        return list_main(args.type, args.json)
    if command == "add":
        return _run(lambda: _show(credentials.add_repository(
            args.url, args.type, args.credential, args.none_needed, args.shared, args.project)))
    if command == "assign":
        if bool(args.credential) == bool(args.none_needed):
            print("error: give a credential id, or --none-needed", file=sys.stderr)
            return 2
        return _run(lambda: _show(credentials.assign_credential(
            args.repository, args.credential, args.none_needed, args.type)))
    if command == "unassign":
        return _run(lambda: _show(credentials.unassign_credential(args.repository)))
    if command == "check":
        return _run(lambda: _show(credentials.check_repository(args.repository)))
    if command == "delete":
        return _run(lambda: credentials.delete_repository(args.repository))
    if command == "share":
        return _run(lambda: _show(credentials.set_repository_shared(
            args.repository, True, args.credential, args.none_needed)))
    if command == "unshare":
        return _run(lambda: _show(credentials.set_repository_shared(args.repository, False)))
    if command == "link":
        return _run(lambda: _show(credentials.link_repository(args.repository, args.project)))
    if command == "unlink":
        return _run(lambda: _show(credentials.unlink_repository(args.repository, args.project)))
    return 2
