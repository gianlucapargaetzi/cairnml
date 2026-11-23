# Changelog

Each release has a section headed "## <version>". The server's "What's new" bundle and the release check read these
sections: a release needs its section here.

## 0.0.6

- The agent calls the server through the SDK's generated request classes, with no version of its own.
- Queue switching (force_workers on or off) is kept; owner impersonation, cluster reports and dynamic GPU allocation are removed.
- ROCm detection comes from the SDK (cairnml.utilities.gpu_query).
- Installed from the SDK's main in CI; the release needs a published SDK release that requirements.txt accepts.
- init writes only the enrolment's settings to the agent configuration, not a copy of the packaged defaults, so an upgrade changes the defaults (job image included); `cairnml-agent doctor` warns about a file an older version wrote with the copy and names the step to remove it.
- Docker jobs of an agent and SDK installed from wheels run: their wheels are repacked from the installed files, and a job that cannot be launched ends failed with the reason.
