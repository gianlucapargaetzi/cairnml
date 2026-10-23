""" cairnml-init: enrol this machine with the server. Reads the server address from the command line.

The same set as the mlops client: cairnml-init (enrol), cairnml-whoami and cairnml-logout live in
cairnml.cli.enrolment. This module keeps the configuration writer and the host helpers they use.
"""
import sys
from typing import Any, Optional, Tuple
from urllib.parse import ParseResult, urlparse

from pathlib2 import Path


def write_config(conf_file, api_host: str, web_host: str, device_id: Optional[str]) -> None:
    """Write the SDK configuration. It names the device; it holds no credentials."""
    # noinspection PyBroadException
    try:
        default_sdk_conf = Path(__file__).absolute().parents[2] / "config/default/sdk.conf"
        with open(str(default_sdk_conf), "rt") as f:
            default_sdk = f.read()
    except Exception:
        print("Error! Could not read default configuration file")
        return
    # noinspection PyBroadException
    try:
        with open(str(conf_file), "wt") as f:
            header = (
                "# CairnML SDK configuration file\n"
                "api {\n"
                "    # Notice: 'api_server' is the api server (default port 8008), not the web server.\n"
                "    api_server: %s\n"
                "    web_server: %s\n"
                "    # This machine signs in with the device key in ~/.cairnml/device.key (empty when signed out)\n"
                "    idp {\n"
                '        device_id: "%s"\n'
                "    }\n"
                "}\n"
                "sdk "
                % (api_host, web_host, device_id or "")
            )
            f.write(header)
            f.write(default_sdk)
    except Exception:
        print(f"Error! Could not write configuration file at: {conf_file}")
        return


def parse_known_host(parsed_host: Any) -> Tuple[str, str]:
    if parsed_host.netloc.startswith("demoapp."):
        # this is our demo server
        api_host = parsed_host.scheme + "://" + parsed_host.netloc.replace("demoapp.", "demoapi.", 1) + parsed_host.path
        web_host = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path
    elif parsed_host.netloc.startswith("app."):
        # this is our application server
        api_host = parsed_host.scheme + "://" + parsed_host.netloc.replace("app.", "api.", 1) + parsed_host.path
        web_host = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path
    elif parsed_host.netloc.startswith("demoapi."):
        print(f"{parsed_host.netloc} is the api server, we need the web server. Replacing 'demoapi.' with 'demoapp.'")
        api_host = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path
        web_host = parsed_host.scheme + "://" + parsed_host.netloc.replace("demoapi.", "demoapp.", 1) + parsed_host.path
    elif parsed_host.netloc.startswith("api."):
        print(f"{parsed_host.netloc} is the api server, we need the web server. Replacing 'api.' with 'app.'")
        api_host = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path
        web_host = parsed_host.scheme + "://" + parsed_host.netloc.replace("api.", "app.", 1) + parsed_host.path
    elif parsed_host.port == 8008:
        print("Port 8008 is the api port. Replacing 8008 with 8080 for Web application")
        api_host = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path
        web_host = parsed_host.scheme + "://" + parsed_host.netloc.replace(":8008", ":8080", 1) + parsed_host.path
    elif parsed_host.port == 8080:
        print("Port 8080 is the web port. Replacing 8080 with 8008 for API server")
        api_host = parsed_host.scheme + "://" + parsed_host.netloc.replace(":8080", ":8008", 1) + parsed_host.path
        web_host = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path
    elif parsed_host.port is None:
        print("Web app hosted on standard port using " + parsed_host.scheme + " protocol.")
        print("Assuming the api port is 8008 and uses the same (" + parsed_host.scheme + ") protocol")
        api_host = parsed_host.scheme + "://" + parsed_host.netloc + ":8008" + parsed_host.path
        web_host = parsed_host.scheme + "://" + parsed_host.netloc + parsed_host.path
    else:
        print("Warning! Could not parse host name")
        api_host = ""
        web_host = ""

    return api_host, web_host


def input_url(host_type: str, host: str = None) -> str:
    while True:
        host_str = f"[{host}] " if host else ""
        print(f"{host_type} configured to: {host_str}", end="")
        parse_input = input()
        if host and (not parse_input or parse_input.lower() == "yes" or parse_input.lower() == "y"):
            break
        parsed_host = verify_url(parse_input) if parse_input else None
        if parse_input and parsed_host:
            host = f"{parsed_host.scheme}://{parsed_host.netloc}{parsed_host.path}"
            break
    return host


def verify_url(parse_input: str) -> Optional[ParseResult]:
    # noinspection PyBroadException
    try:
        if not parse_input.startswith("http://") and not parse_input.startswith("https://"):
            # if we have a specific port, use http prefix, otherwise assume https
            if ":" in parse_input:
                parse_input = "http://" + parse_input
            else:
                parse_input = "https://" + parse_input
        parsed_host = urlparse(parse_input)
        if parsed_host.scheme not in ("http", "https"):
            parsed_host = None
    except Exception:
        parsed_host = None
        print(f"Could not parse url {parse_input}\nEnter your cairnml-server host: ", end="")
    return parsed_host


def main(argv: Optional[list] = None) -> None:
    from cairnml.cli.enrolment import init_main

    sys.exit(init_main(argv))


if __name__ == "__main__":
    main()
