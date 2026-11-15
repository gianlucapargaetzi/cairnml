# 0001. Replace embedded third-party code by upstream packages

## Status

Accepted (2026-11-15). Tracked in cairnml#59 (review of the embedded third-party code) and its replacement issues.

## Context

The SDK, derived from the ClearML SDK, carried copies of several third-party libraries inside its own package: NVIDIA's `pynvml`, `gpustat`, `pyrsmi` (AMD), `plotlympl` (a matplotlib-to-plotly exporter), `pyhocon`, `jsonmodels`, `requests_toolbelt`, and `pigar`, plus some small helpers. Nothing in the repository recorded why each copy was there or which upstream version it came from. The copies received no upstream security or bug fixes unless someone ported them by hand, they added size to the wheel, and they appeared in code search and in code-analysis tools as if they were this project's code.

## Decision

Every embedded copy is replaced by its official upstream package:

- `pyhocon` (with `pyparsing>=3`), `jsonmodels` and `requests-toolbelt` are base dependencies.
- `pigar>=2.2,<3` is a base dependency. Its import analysis runs in a child process (`python -I`), so its changes to `sys.path` and `sys.modules` never reach the training process.
- GPU monitoring is the extra `cairnml[gpu]`, which installs `nvidia-ml-py` and `gpustat`. NVIDIA is read through `gpustat`.
- AMD GPUs are read through AMD's own Python API, `amdsmi`. It is **not** a pip dependency: it comes with the ROCm installation, and the SDK imports it only when the host has the ROCm kernel driver. The AMD reader is not yet run on AMD hardware.
- Interactive matplotlib plots use `plotly.matplotlylib` from the extra `cairnml[plot]`. Without the extra, figures are sent as images.
- Dead and shim code (`astor_unparse`, `cli_table`, the `attrs` wrapper, `distutils_version`) is deleted.

## Alternatives considered

- **Keep the copies.** Rejected: no upstream fixes, no record of their origin, and the wheel carries code this project does not maintain.
- **Make `gpustat`, `plotly` or `amdsmi` base dependencies.** Rejected for `plotly` and `gpustat`, which are only needed for monitoring and plots; the extras keep the base install small. Rejected for `amdsmi`: its only PyPI copy is an unofficial, older redistribution, and AMD ships the official library with ROCm.
- **Wait for the git master of gpustat.** Rejected: its AMD support is not on PyPI yet, so waiting would keep the copy in place indefinitely.

## Consequences

- A GPU query takes about 100 ms with `gpustat`, against about 1 ms with the embedded copy. The monitor samples about twice a second, so the cost is a sleep in the monitor thread, not CPU time. Accepted.
- Interactive plot JSON changes slightly: the trace types and counts match, but some layout keys differ. The plots were checked against the embedded copy's output on five reference figures; a browser check of the web app was not run in this environment.
- The HOCON text the SDK writes changes its list form (one item per line). Both forms read back to the same configuration.
- Installing AMD monitoring needs the ROCm installation (`amd-smi-lib`), documented in `docs/amd-gpu-monitoring.md`.
- The ADR and the notices file are the record of these decisions; the replacement issues (cairnml#78 to #84, and #82 for plots) are the history.

## Switch-over trigger

When PyPI carries a `gpustat` release with AMD support (through `rocmi`), the AMD reader in `cairnml/utilities/gpu_query.py` is replaced by `gpustat.new_query()`, as described in `docs/amd-gpu-monitoring.md`.
