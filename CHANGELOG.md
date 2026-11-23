# Changelog

Each release has a section headed "## <version>". The server's "What's new" bundle and the release check read these
sections: a release needs its section here.

## 0.0.9

Works with server release 0.0.6 (API 2.41) and later.

- cairnml init writes only the enrolment's settings to the configuration file, not a copy of the packaged defaults, so an upgrade changes the defaults; cairnml doctor warns about a file an older version wrote with the copy and names the step to remove it.
- A process forked by Task.init, such as the resource monitor, no longer sends its requests on the parent's keep-alive connections, so a response could reach the wrong request (models.create received the body of tasks.get_by_id).
- The dvc next to the Python interpreter is used first, then the one on the PATH; when neither has one, the error names both places searched.
- A false value in the environment settings of the configuration is set as its text, "0" or "False", not as an empty value.
- A configuration entry can set the permission bits of the file it writes with mode, as octal digits: 600 or "0600"; a mode that cannot be applied is reported and the file is kept.
- set_user_properties sends its hyper-parameters as a list; before, a pipeline failed when it set its version.
- A script that queues itself with execute_remotely no longer prints "No progress bar named ... was started" right before "Switching to remote execution".
- When gpustat is the missing import, the message to install cairnml[gpu] is shown.
- A script whose log reporter process starts while the repository check is still running no longer waits for the check's full 300-second timeout when its task closes.
- The packages that the function of a pipeline step imports are recorded again when the SDK, or another package the function imports, is installed from a local folder (pip install -e); before, only cairnml was recorded and the step failed on the agent with a missing module.
- When the scan of a pipeline step's imports fails or misses an import of the function, the SDK logs an error that says which import and to pass packages= on the component, instead of a cut-off warning or nothing.
- Agent: the agent is the cairnml[agent] extra; uninstall the old cairnml-agent package before installing it (pip uninstall cairnml-agent).
- Agent: queue switching (force_workers on or off) is kept; owner impersonation, cluster reports and dynamic GPU allocation are removed.
- Agent: cairnml-agent init writes only the enrolment's settings to the agent configuration, not a copy of the packaged defaults, so an upgrade changes the defaults (job image included); cairnml-agent doctor warns about a file an older version wrote with the copy and names the step to remove it.
- Agent: Docker jobs of an agent and SDK installed from wheels run: their wheels are repacked from the installed files, and a job that cannot be launched ends failed with the reason.

## 0.0.8

- The client is generated from the server's schema (one API version, 2.41); the versioned client modules are gone.
- The API version constant and the version checks follow the server's version.
- Datachunk uploads use the API version the server serves them at.
- Pinned to the server through its main branch: the client CI compares with the server's schema on main.
