import re
import subprocess
import sys

from cairnml_agent.interface import get_parser


def subcommand_parser(name):
    parser = get_parser()
    return next(a for a in parser._actions if a.choices).choices[name]


def test_the_config_file_help_names_the_file_the_agent_writes():
    text = re.sub(r'\s+', '', get_parser().format_help())  # the help wraps the path onto two lines
    assert '~/cairnml-agent.conf' in text
    assert '~/cairnml.conf' not in text


def test_the_banner_names_cairnml_and_not_the_upstream_tagline():
    from cairnml_agent.interface.base import HEADER
    assert HEADER.startswith('CAIRNML-AGENT')
    assert 'Orchestration' not in HEADER


def test_the_daemon_help_shows_the_common_options_first():
    groups = subcommand_parser('daemon')._action_groups
    titles = [group.title for group in groups]
    assert titles.index('Common options') < titles.index('Docker support') < titles.index('Other options')
    common = next(group for group in groups if group.title == 'Common options')
    assert {'--queue', '--docker', '--gpus'} <= {a.option_strings[0] for a in common._group_actions}


def test_building_the_parser_twice_keeps_the_daemon_groups():
    for _ in range(2):
        titles = [group.title for group in subcommand_parser('daemon')._action_groups]
        assert 'Common options' in titles


def test_importing_the_requirement_parser_prints_no_pkg_resources_warning():
    code = "import cairnml_agent.external.requirements_parser.requirement"
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert 'pkg_resources' not in result.stderr
