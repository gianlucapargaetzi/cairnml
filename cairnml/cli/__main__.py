"""cairnml: the SDK's command line. One command with a subcommand for each tool, like cairnml-agent:

    cairnml init <server>      enrol this machine with the server
    cairnml dataset ...        versioned datasets on git and DVC

Each subcommand is the tool it names; this module only hands it the rest of the command line.
"""
import importlib
import sys
from typing import List, Optional

# command -> (module, function, what it does)
COMMANDS = {
    "init": ("cairnml.cli.enrolment", "init_main", "enrol this machine with the CairnML server"),
    "whoami": ("cairnml.cli.enrolment", "whoami_main", "show who this machine signs in as"),
    "logout": ("cairnml.cli.enrolment", "logout_main", "revoke this machine's enrolment and remove its key"),
    "dataset": ("cairnml.cli.dataset.__main__", "main", "versioned datasets on git and DVC"),
    "task": ("cairnml.cli.task.__main__", "main", "launch any codebase on a machine running cairnml-agent"),
    "param-search": ("cairnml.cli.hpo.__main__", "main", "search for the best parameters for your models"),
    "debug": ("cairnml.cli.debug.__main__", "main", "debugging tools for the CairnML SDK"),
}


def usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = ["usage: cairnml <command> [arguments]", "", "commands:"]
    lines += [f"  {name.ljust(width)}  {what}" for name, (_, _, what) in COMMANDS.items()]
    lines += ["", "Run 'cairnml <command> --help' for the arguments of a command."]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(usage())
        return 0 if args else 2
    name = args[0]
    if name not in COMMANDS:
        print(f"cairnml: unknown command '{name}'\n\n{usage()}", file=sys.stderr)
        return 2
    module, function, _ = COMMANDS[name]
    # The tools read sys.argv themselves; their usage line then says "cairnml <command>".
    sys.argv = [f"cairnml {name}", *args[1:]]
    result = getattr(importlib.import_module(module), function)()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
