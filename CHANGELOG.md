# Changelog

Each release has a section headed "## <version>". The server's "What's new" bundle and the release check read these
sections: a release needs its section here.

## 0.0.8

- The client is generated from the server's schema (one API version, 2.41); the versioned client modules are gone.
- The API version constant and the version checks follow the server's version.
- Datachunk uploads use the API version the server serves them at.
- Pinned to the server through its main branch: the client CI compares with the server's schema on main.
