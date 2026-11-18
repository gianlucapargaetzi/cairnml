"""Enrols a machine with the device sign-in, the way a person does on a headless machine: the command prints a short code
and an address, and a browser opens that address, signs in and approves the device.

The browser is headless Chromium from Playwright (see CONTRIBUTING.md). Only the test's own variables are passed to the
machine: the CAIRNML_ and TRAINS_ variables of the test run would otherwise point the command at another home or server.
"""

import os
import re
import subprocess
from pathlib import Path

ADDRESS = re.compile(r"To sign in, open (\S+) on any device")


def machine_environment(home: Path) -> dict:
    """The environment of a command that acts as the machine in home: its own HOME, and nothing of the test run's."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("CAIRNML_", "TRAINS_"))}
    env["HOME"] = str(home)
    # the sign-in code is read from a pipe: unbuffered, or the line stays in the machine's buffer until it exits
    env["PYTHONUNBUFFERED"] = "1"
    return env


def approve(address: str, user: str, password: str) -> None:
    """Signs in on the sign-in page at address and approves the device, as the person would."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(address)
            if page.locator("#username").count():
                page.fill("#username", user)
                page.fill("#password", password)
                page.click("#kc-login")
                page.wait_for_load_state("networkidle")
            for selector in ("input[name=accept]", "button:has-text('Yes')"):
                if page.locator(selector).count():
                    page.click(selector)
                    page.wait_for_load_state("networkidle")
                    break
        finally:
            browser.close()


def enrol(binary: str, home: Path, server: str, name: str, user: str, password: str, timeout: float = 300) -> str:
    """Runs ``binary init <server> --device --name <name>`` as the machine in home and approves its code in a browser.

    Returns the command's output. Raises RuntimeError with that output when the command fails or shows no code.
    """
    home.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [binary, "init", server, "--device", "--name", name],
        env=machine_environment(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = []
    address = None
    try:
        for line in process.stdout:
            lines.append(line)
            match = ADDRESS.search(line)
            if match:
                address = match.group(1)
                break
        if address is None:
            rest, _ = process.communicate(timeout=timeout)
            raise RuntimeError("the machine showed no sign-in code:\n" + "".join(lines) + rest)
        approve(address, user, password)
        rest, _ = process.communicate(timeout=timeout)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    output = "".join(lines) + rest
    if process.returncode != 0:
        raise RuntimeError(f"{binary} init failed with status {process.returncode}:\n{output}")
    return output
