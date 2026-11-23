# Contributing

## Unit tests

The unit tests mock the server and need nothing else:

The agent's tests are in `tests/agent/` of the SDK repository, next to the SDK's own tests in `tests/`. They run from the
repository root with the agent extra installed:

```bash
python -m pip install -e ".[agent,dataset,router]" -r tests/agent/requirements.txt
python -m pytest tests --ignore=tests/agent -q
python -m pytest tests/agent -q --ignore=tests/agent/requirements --ignore=tests/agent/package
python -m pytest tests/agent/package -q
```

Tests marked `integration` are skipped in those runs. The `tests/agent/package` folder needs no built wheel; CI runs it
in the same step as the rest of the agent tests.

## Integration tests

The integration tests run the agent against a real CairnML server: an agent enrols, its daemon registers as a worker on
its own queue, and a job from a git repository runs to `completed`. They are in `tests/agent/integration/` and carry the
`integration` marker. They are skipped unless these variables are set:

| Variable | What it is |
|---|---|
| `CAIRNML_TEST_SERVER` | the server's web address, for example `http://localhost/cairnml` |
| `CAIRNML_TEST_ADMIN_USER` | the sign-in name of a CairnML admin (only an admin may enrol an agent) |
| `CAIRNML_TEST_ADMIN_PASSWORD` | that admin's password; the tests never print it |

Setup, once per machine:

- The server must be running at the address, on this host or reachable from it. The stack is started from the
  server repository with `deploy/scripts/bootstrap.sh`.
- The tests enrol their own scratch homes with the device sign-in, which a headless Chromium approves. Install the
  browser with `python -m playwright install chromium`, and install `playwright` in the environment of the tests.
- Git and Docker are not needed by the job itself (the job runs in a virtual environment on this host), but
  `cairnml-agent doctor` checks that Docker is installed and running, so a host without Docker fails that check.

Run them with the environment of the agent under test, from the repository root:

```bash
CAIRNML_TEST_SERVER=http://localhost/cairnml \
CAIRNML_TEST_ADMIN_USER=<admin> CAIRNML_TEST_ADMIN_PASSWORD=<password> \
python -m pytest -m integration tests/agent/integration -v -s
```

The tests run in file order and form one chain: a later check needs what an earlier one leaves, and fails with the
name of the check that should have left it. The job check prints how long the job took. `-s` shows that line.

The tests use the `cairnml` and `cairnml-agent` commands of the environment they run in, so the agent under test is the
one that is installed there. A job's virtual environment installs the SDK from the SDK's source tree that the
environment has. The scratch homes live in pytest's temporary directory and are not kept.
