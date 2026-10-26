"""cairnml-agent init: enrol this agent machine with the server. Reads the server address from the command line.

It does what cairnml-init of the CairnML SDK does, with one difference: the machine is enrolled as an agent, and only an
admin may do that. The server address is parsed the same way (parse_known_host is the SDK's, copied so the agent does
not depend on the SDK).
"""
from __future__ import print_function

import os
import sys

import requests

from .._vendor.pathlib2 import Path
from .._vendor.six.moves.urllib.parse import urlparse

from cairnml_agent.backend_config.defs import LOCAL_CONFIG_FILE_OVERRIDE_VAR, LOCAL_CONFIG_FILES
from cairnml_agent.external.pyhocon import ConfigFactory
from cairnml_agent.idp.enrol import EnrolmentFailed, enrol


def default_config_file():
    return LOCAL_CONFIG_FILE_OVERRIDE_VAR.get() or LOCAL_CONFIG_FILES[-1]


def main(server, name=None, force=False, file=None):
    conf_file = Path(os.path.expanduser(file or default_config_file())).absolute()
    previous_device_id = _device_id_in(conf_file)

    api_server, _ = parse_known_host(urlparse(server))
    try:
        result = enrol(api_server or server, kind='agent', name=name, force=force,
                       previous_device_id=previous_device_id)
    except EnrolmentFailed as exc:
        print('error: {}'.format(exc), file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print('error: cannot reach {}: {}'.format(api_server or server, exc.__class__.__name__), file=sys.stderr)
        return 1

    if not write_config(conf_file, result.server_url, result.web_url, result.device_id):
        return 1
    print('enrolled as {} on {} (device {})'.format(result.user or 'unknown user', result.server_url, result.device_id))
    return 0


def _device_id_in(conf_file):
    if not conf_file.is_file():
        return None
    try:
        return ConfigFactory.parse_file(str(conf_file)).get('api.idp.device_id', None) or None
    except Exception:
        return None


def write_config(conf_file, api_host, web_host, device_id):
    """Write the agent configuration. It names the device; it holds no credentials."""
    # noinspection PyBroadException
    try:
        conf_folder = Path(__file__).parent.absolute() / '..' / 'backend_api' / 'config' / 'default'
        default_conf = ''
        for conf in ('agent.conf', 'sdk.conf', ):
            with open(str(conf_folder / conf), 'rt') as f:
                default_conf += conf.split('.')[0] + ' ' + f.read() + '\n'
    except Exception:
        print('Error! Could not read default configuration file')
        return False
    # noinspection PyBroadException
    try:
        with open(str(conf_file), 'wt') as f:
            header = '# CAIRNML-AGENT configuration file\n' \
                     'api {\n' \
                     '    # Notice: \'api_server\' is the api server (default port 8008), not the web server.\n' \
                     '    api_server: %s\n' \
                     '    web_server: %s\n' \
                     '    # This agent signs in with the device key in ~/.cairnml/agent/device.key\n' \
                     '    idp {\n' \
                     '        device_id: "%s"\n' \
                     '    }\n' \
                     '}\n\n' % (api_host, web_host, device_id)
            f.write(header)
            # Git credentials and extra package indexes are left blank; edit this file to set them.
            f.write('# Set GIT user/pass credentials\n'
                    '# leave blank for GIT SSH credentials\n'
                    'agent.git_user=""\n'
                    'agent.git_pass=""\n'
                    '\n'
                    '# extra_index_url: ["https://cairnml.jfrog.io/cairnml/api/pypi/public/simple"]\n'
                    'agent.package_manager.extra_index_url= [\n\n]\n\n')
            f.write(default_conf)
    except Exception:
        print('Error! Could not write configuration file at: {}'.format(str(conf_file)))
        return False
    return True


def parse_known_host(parsed_host):
    """The API and web addresses of the server. The server is reached through its proxy on the address given: the web
    app at that address, the API under /api."""
    root = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path.rstrip("/")
    return root + "/api", root
