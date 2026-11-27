# Changelog

Each release has a section headed "## <version>". The server's "What's new" bundle and the release check read these
sections: a release needs its section here.

## 0.1.0

Works with server release 0.1.0 (API 2.41) and later.

- cairnml init writes only the enrolment's settings to the configuration file, not a copy of the packaged defaults, so an upgrade changes the defaults; cairnml doctor warns about a file an older version wrote with the copy and names the step to remove it.
- A process forked by Task.init, such as the resource monitor, no longer sends its requests on the parent's keep-alive connections, so a response could reach the wrong request (models.create received the body of tasks.get_by_id).
- HyperParameterOptimizer.stop() called after wait() no longer waits for the reporter's next report period (5 minutes by default); it returns at once.
- The dvc next to the Python interpreter is used first, then the one on the PATH; when neither has one, the error names both places searched.
- cairnml-agent doctor words its GitHub warning as a fact (the server has no company GitHub App) and says it is moot when the project owner has a credential, instead of telling every user to add one.
- A false value in the environment settings of the configuration is set as its text, "0" or "False", not as an empty value.
- A configuration entry can set the permission bits of the file it writes with mode, as octal digits: 600 or "0600"; a mode that cannot be applied is reported and the file is kept.
- cairnml dataset doctor reports a repository that git cannot read as a failed check that names the cause and the sign-in step, instead of calling it empty (the check shipped in 0.0.8).
- set_user_properties sends its hyper-parameters as a list; before, a pipeline failed when it set its version.
- A script that queues itself with execute_remotely no longer prints "No progress bar named ... was started" right before "Switching to remote execution".
- NVIDIA GPU monitoring needs no extra: nvidia-ml-py and gpustat are installed with cairnml, so a task on a machine with an NVIDIA GPU reports its GPU curves after a plain pip install. The extra gpu still installs and adds nothing. gpustat has no wheel on PyPI, so pip builds it during the install. When one of the two packages is missing from the environment, the task log says which command installs them. A machine with no NVIDIA or AMD driver is left unmonitored and the monitor says nothing about it.
- A script whose log reporter process starts while the repository check is still running no longer waits for the check's full 300-second timeout when its task closes.
- The packages that the function of a pipeline step imports are recorded again when the SDK, or another package the function imports, is installed from a local folder (pip install -e); before, only cairnml was recorded and the step failed on the agent with a missing module.
- When the scan of a pipeline step's imports fails or misses an import of the function, the SDK logs an error that says which import and to pass packages= on the component, instead of a cut-off warning or nothing.
- The Optuna optimizer frees the slot of a trial soon after its job has finished, not after the next poll period (2 minutes by default), so a search of short trials ends sooner. It checks a running trial about every 5 seconds, and the first value of a trial can come up to one poll period after the trial started.
- The Optuna optimizer reports a step of a trial to Optuna once, so a search no longer prints the warning that the reported value is ignored because the step is already reported.
- A task parameter of an option declared with argparse.BooleanOptionalAction (--flag/--no-flag) reaches the script as True or False, not as a list or None, and the agent no longer logs "Failed parsing task parameter" for it.
- Agent: the agent is the cairnml[agent] extra; uninstall the old cairnml-agent package before installing it (pip uninstall cairnml-agent).
- Agent: queue switching (force_workers on or off) is kept; owner impersonation, cluster reports and dynamic GPU allocation are removed.
- Agent: cairnml-agent init writes only the enrolment's settings to the agent configuration, not a copy of the packaged defaults, so an upgrade changes the defaults (job image included); cairnml-agent doctor warns about a file an older version wrote with the copy and names the step to remove it.
- Agent: Docker jobs of an agent and SDK installed from wheels run: their wheels are repacked from the installed files, and a job that cannot be launched ends failed with the reason.
- Agent: a job container gets nothing from the machine's SSH or Git setup: no SSH agent socket or SSH_AUTH_SOCK, no copy of ~/.ssh, no ~/.git-credentials and no ~/.gitconfig. A task's or an administrator's own docker arguments are unchanged.
- Agent: a job's repository is read with the token the server issues for it, or without a credential when it is public, and with nothing else. The agent's git commands run without the machine's SSH keys, SSH agent, Git configuration, credential helpers and askpass programs. A GitHub address in ssh form is cloned over https with the server's token; any other ssh address fails and asks for the https address. When the server refuses or does not answer the credential request the job fails (before, the agent fell back to configured credentials). A private repository without a token fails the job with a message that names the repository and says to assign a credential under Repositories and Datasets in the web app or to install the GitHub App. The token is sent only to the host of the repository's address, not to a submodule on another host. A password written in the task's own repository address is still used.
- Agent: the settings git_user, git_pass, git_host, git extra_credentials, force_git_ssh_protocol, force_git_ssh_port, force_git_ssh_user, disable_ssh_mount, translate_ssh and translate_ssl_replacement_scheme and the environment variables CAIRNML_AGENT_GIT_USER, CAIRNML_AGENT_GIT_PASS, CAIRNML_AGENT_GIT_HOST, CAIRNML_AGENT_DISABLE_SSH_MOUNT and CAIRNML_AGENT_SSH_URL_REPLACEMENT_SCHEME are removed and ignored; the agent warns once at start when one is set. The autoscaler's cloud driver and the Kubernetes glue no longer write Git credentials into the configuration of the machines and pods they start, and the CAIRNML_AUTOSCALER_GIT_USER and CAIRNML_AUTOSCALER_GIT_PASSWORD variables are gone.
- The warning after enqueueing to a queue that no agent serves, the cairnml doctor hint and the agent documentation no longer advise cairnml-agent daemon --queue <name> for it: an agent serves only the queue that was made when it was enrolled, so they say that and where the queue's name is listed.

## 0.0.8

- The client is generated from the server's schema (one API version, 2.41); the versioned client modules are gone.
- The API version constant and the version checks follow the server's version.
- Datachunk uploads use the API version the server serves them at.
- Pinned to the server through its main branch: the client CI compares with the server's schema on main.
