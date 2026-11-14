"""Names vulture reports that are deliberate. Each line says why it is kept; anything not listed here fails the dead-code check.

Run: vulture cairnml --min-confidence 80 --exclude '*_vendor*,*/external/*,*backend_api/services*,*/tests/*,*pyhocon*,*pynvml*' vulture_whitelist.py
"""

# Parameters fixed by an interface or an override: the signature must stay, the body does not need them.
checker  # _is_array(checker, instance) is a jsonschema type checker callback
old_token  # _do_refresh_token(old_token, exp): TokenManager abstract method signature
pool_kwargs  # TLSv1HTTPAdapter.init_poolmanager: HTTPAdapter override signature
exc_val  # __exit__(exc_type, exc_val, exc_tb): context manager protocol
exc_tb  # __exit__(exc_type, exc_val, exc_tb): context manager protocol
etype  # custom excepthook(shell, etype, value, tb, tb_offset): IPython protocol
tb  # excepthook / __exit__ signatures: exception protocol
tb_offset  # excepthook signature: IPython protocol
update_task_design  # Task update method: callers pass it by name
a_bad_color  # TensorFlow image summary hook: signature fixed by the framework callback
a_max_images  # TensorFlow image summary hook: signature fixed by the framework callback
sizehint  # file-like read(size, sizehint) signature used by the storage helper's stream wrapper
progress_callback  # upload/download methods: callers pass it by name
verify_hash  # download method: callers pass it by name
fill_axis  # plotly helper signature used by callers by keyword
extent  # matplotlib exporter renderer hooks: signature from the mplexporter interface
bars  # draw_bars(bars) renderer hook: signature from the mplexporter interface

# Imports kept on purpose.
omegaconf  # import probe: hydra_bind uses it only to detect the optional package
HTTPConnection  # compatibility re-export of requests_toolbelt (kept for callers importing it from _compat)
