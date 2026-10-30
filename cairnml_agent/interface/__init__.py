import itertools
from functools import partial
from importlib import import_module
import argparse

from cairnml_agent.definitions import PROGRAM_NAME
from .base import Parser, base_arguments, add_service, OnlyPluralChoicesHelpFormatter

SERVICES = [
    'worker',
]


def get_parser():
    top_parser = Parser(
        prog=PROGRAM_NAME,
        add_help=False,
        formatter_class=partial(
            OnlyPluralChoicesHelpFormatter,
            max_help_position=120,
            width=120,
        ),
    )
    base_arguments(top_parser)
    from .worker import COMMANDS
    subparsers = top_parser.add_subparsers(dest='command')
    for c in COMMANDS:
        parser = subparsers.add_parser(name=c, help=COMMANDS[c]["help"])
        # Arguments without a group go to "Other options" when the command has groups, so that the groups come first.
        # Copies are grouped: the definitions are shared between commands, and "group" must stay in them.
        args = {name: dict(params) for name, params in COMMANDS[c].get("args", {}).items()}
        default_group = "Other options" if any("group" in v for v in args.values()) else ""
        groups = itertools.groupby(
            sorted(args.items(), key=lambda x: x[1].get("group", default_group)),
            key=lambda x: x[1].pop("group", default_group),
        )
        for group_name, group in groups:
            p = parser if not group_name else parser.add_argument_group(group_name)
            for key, value in group:
                aliases = value.pop("aliases", [])
                p.add_argument(key, *aliases, **value)

    return top_parser
