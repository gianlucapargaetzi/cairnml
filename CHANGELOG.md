# Changelog

Each release has a section headed "## <version>". The server's "What's new" bundle and the release check read these
sections: a release needs its section here.

## 0.0.9

- Every API call of the SDK is sent as its generated request class, at the API level negotiated with the server, with
  exactly the fields of the schema. The hand-built request paths and bodies are gone.
- The client is regenerated from the server's schema (API 2.41): each endpoint's definition is picked by version from
  the generated client, and the generated modules no longer import what they do not use.
- The job's device git token is sent at its generated version, and `cairnml init` takes the API version of its
  enrolment calls from the request classes.
- A forked process gets its own HTTP connection pool; a subclass can set the connection retries of the start-up
  sign-in; `dvc` next to the interpreter is preferred to the one on the PATH.
- The configuration applies file modes and keeps false environment values as the agent does (the agent's copy of this
  code is gone); the working directory of local repositories is defaulted once; offline export and import no longer read
  the removed version setting.
- Defects found by the static check against the schema are fixed, and dead plumbing (the async header, the old header
  names, the client's version attribute) is removed.
- Built for the server release 0.0.6 (API 2.41): the client CI compares with the server's schema and the publish
  workflow refuses a client generated from an unreleased server schema. The SDK's real-server checks run as integration
  tests.
- `cairnml init` writes only the enrolment's settings to the configuration file, not a copy of the packaged defaults, so an upgrade changes the defaults; `cairnml doctor` warns about a file an older version wrote with the copy and names the step to remove it.

## 0.0.8

- The client is generated from the server's schema (one API version, 2.41); the versioned client modules are gone.
- The API version constant and the version checks follow the server's version.
- Datachunk uploads use the API version the server serves them at.
- Pinned to the server through its main branch: the client CI compares with the server's schema on main.
