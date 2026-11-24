"""The cairnml-agent console script: stops with one line when the agent extra is missing, else runs the agent."""

import importlib.metadata
import sys

# the distributions the agent needs that the SDK's base install does not bring
AGENT_ONLY = ["virtualenv", "requirements-parser", "setuptools", "packaging"]
if sys.platform == "win32":
    AGENT_ONLY.append("pywin32")


def missing_agent_distributions():
    missing = []
    for name in AGENT_ONLY:
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return missing


def require_agent_extra():
    missing = missing_agent_distributions()
    if missing:
        sys.stderr.write(
            'cairnml-agent needs the agent extra: pip install "cairnml[agent]" (missing: {})\n'.format(
                ", ".join(missing)
            )
        )
        sys.exit(1)


def main():
    require_agent_extra()
    from cairnml_agent.__main__ import main as agent_main

    return agent_main()
