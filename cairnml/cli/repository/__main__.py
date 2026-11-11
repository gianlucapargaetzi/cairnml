"""cairnml repository: the code repositories your runs use, as the web app lists them under Datasets & Repositories.

    cairnml repository list [--json]

One line for each project that uses a repository: the repository, the project, who owns it there, how it is read now
(with a credential, "None needed", without one because it is public, through the company's App, or not at all) and the
id of the credential.
"""

import json
import sys
from argparse import ArgumentParser

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


def _lines(repositories: list) -> list:
    """(repository, project, owner, state, credential, message) for each binding of each repository."""
    lines = []
    for repository in repositories:
        name = f"{repository.get('host', '')}/{repository.get('path', '')}".strip("/")
        for binding in repository.get("bindings") or []:
            lines.append((
                name,
                binding.get("project_name") or binding.get("project") or "(no project)",
                binding.get("owner_name") or binding.get("owner") or "",
                READ_AS.get(binding.get("state"), binding.get("state") or ""),
                binding.get("credential") or "",
                binding.get("message") or "",
            ))
    return lines


def list_main(as_json: bool = False) -> int:
    try:
        repositories = credentials.fetch_repository_list()
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(repositories, indent=2))
        return 0
    lines = _lines(repositories)
    if not lines:
        print("No repositories yet. A repository is listed once a run in one of your projects first uses it.")
        return 0
    print(table(("REPOSITORY", "PROJECT", "OWNER", "READ AS", "CREDENTIAL"), [line[:5] for line in lines]))
    problems = [line for line in lines if line[3] in ("not readable", "unknown")]
    for repository, project, _owner, _state, _credential, message in problems:
        print(f"\n{repository} ({project}): {message}")
    return 0


def main() -> int:
    parser = ArgumentParser(prog="cairnml repository", description="the code repositories your runs use")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list the repositories your runs use, and how each is read",
                             description="list the repositories your runs use, one line for each project that uses one")
    listing.add_argument("--json", action="store_true", help="print the list as the server returns it")
    args = parser.parse_args()
    if args.command == "list":
        return list_main(as_json=args.json)
    return 2
