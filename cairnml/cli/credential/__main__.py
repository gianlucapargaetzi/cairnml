"""cairnml credential: the credentials you can use for datasets. A dataset is read with the credential it was added with:
one of your own GitHub Apps or tokens, or the company's GitHub App."""

import sys
from argparse import ArgumentParser

from ...datasets import DatasetError
from ...datasets.credentials import list_credentials


def _scope(row: dict) -> str:
    return "company" if row.get("scope") == "company" else "yours"


def list_main() -> int:
    """Lists the credentials. The arguments were parsed by main, which chose this subcommand."""
    try:
        rows = list_credentials()
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("No credentials are available to you. Add one in the web app under Settings > Access.")
        return 0
    headers = ("ID", "TYPE", "NAME", "SCOPE", "OWNER", "VALID FOR", "STATE")
    table = [(r.get("id", ""), r.get("type", ""), r.get("name", ""), _scope(r), r.get("owner", ""),
              r.get("valid_for", ""), r.get("state", "")) for r in rows]
    widths = [max(len(str(line[i])) for line in [headers, *table]) for i in range(len(headers))]
    for line in [headers, *table]:
        print("  ".join(str(value).ljust(width) for value, width in zip(line, widths)).rstrip())
    return 0


def main() -> int:
    parser = ArgumentParser(prog="cairnml credential", description="the credentials you can use for datasets")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the credentials you can use, with their ids",
                   description="list the credentials you can use for datasets, with the id that --credential takes")
    args = parser.parse_args()
    if args.command == "list":
        return list_main()
    return 2
