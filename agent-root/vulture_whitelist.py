"""Names vulture reports that are deliberate. Each line says why it is kept; anything not listed here fails the dead-code check.

Run: vulture cairnml_agent vulture_whitelist.py --min-confidence 80 --exclude '*_vendor*,*/external/*,*backend_api/services*,*/tests/*,*pyhocon*,*pynvml*'
"""

# Parameters fixed by an interface or an override: the signature must stay, the body does not need them.
old_token  # TokenManager._do_refresh_token(old_token, exp): subclasses override it with this signature
pool_kwargs  # TLSv1HTTPAdapter.init_poolmanager(**pool_kwargs): HTTPAdapter override signature
kwds  # custom_template.Template.substitute/safe_substitute(**kwds): string.Template signature callers use
force_current_version  # Worker._get_docker_cmd keyword: kept until cairnml-agent#46 splits that method
