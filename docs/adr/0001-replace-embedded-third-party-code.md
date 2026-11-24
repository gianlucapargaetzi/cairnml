# 0001. Replace embedded third-party code by upstream packages

## Status

Accepted (2026-11-15). Tracked in cairnml#59 (review of the embedded third-party code) and its replacement issues.

Amended (2026-11-24, cairnml#195): the GPU packages are base dependencies, no longer an extra. The decision and the
alternatives below say so; the other decisions stand.

## Context

The SDK, derived from the ClearML SDK, carried copies of several third-party libraries inside its own package: NVIDIA's `pynvml`, `gpustat`, `pyrsmi` (AMD), `plotlympl` (a matplotlib-to-plotly exporter), `pyhocon`, `jsonmodels`, `requests_toolbelt`, and `pigar`, plus some small helpers. Nothing in the repository recorded why each copy was there or which upstream version it came from. The copies received no upstream security or bug fixes unless someone ported them by hand, they added size to the wheel, and they appeared in code search and in code-analysis tools as if they were this project's code.

## Decision

Every embedded copy is replaced by its official upstream package:

- `pyhocon` (with `pyparsing>=3`), `jsonmodels` and `requests-toolbelt` are base dependencies.
- `pigar>=2.2,<3` is a base dependency. Its import analysis runs in a child process (`python -I`), so its changes to `sys.path` and `sys.modules` never reach the training process.
- `nvidia-ml-py` and `gpustat` are base dependencies, so GPU monitoring needs no extra. NVIDIA is read through `gpustat`. The extra `gpu` stays as an empty alias, so that documented install commands keep working. (Until the amendment of 2026-11-24 they were the extra.)
- AMD GPUs are read through AMD's own Python API, `amdsmi`. It is **not** a pip dependency: it comes with the ROCm installation, and the SDK imports it only when the host has the ROCm kernel driver. The AMD reader is not yet run on AMD hardware.
- Interactive matplotlib plots use `plotly.matplotlylib` from the extra `cairnml[plot]`. Without the extra, figures are sent as images.
- Dead and shim code (`astor_unparse`, `cli_table`, the `attrs` wrapper, `distutils_version`) is deleted.

## Alternatives considered

- **Keep the copies.** Rejected: no upstream fixes, no record of their origin, and the wheel carries code this project does not maintain.
- **Make `plotly` or `amdsmi` base dependencies.** Rejected for `plotly`, which is only needed for plots; the extra keeps the base install small. Rejected for `amdsmi`: its only PyPI copy is an unofficial, older redistribution, and AMD ships the official library with ROCm.
- **Keep `gpustat` and `nvidia-ml-py` an extra.** Chosen first, reversed on 2026-11-24 (cairnml#195): a plain `pip install cairnml` on a machine with an NVIDIA GPU gave no GPU curves and a notice, and users had to find the extra. The cost of the reversal is below.
- **Wait for the git master of gpustat.** Rejected: its AMD support is not on PyPI yet, so waiting would keep the copy in place indefinitely.

## Consequences

- Every install now carries `gpustat`, which has no wheel on PyPI, so pip builds it (a pure-Python package; it was built without trouble on Linux with Python 3.12, and CI builds it on every run). Importing it adds about 45 ms to `import cairnml`. The monitor checks once at start whether the machine has an NVIDIA or AMD driver; a machine with none is left unmonitored and the monitor says nothing, so a laptop or a CI runner sees no new message.
- A GPU query takes about 100 ms with `gpustat`, against about 1 ms with the embedded copy. The monitor samples about twice a second, so the cost is a sleep in the monitor thread, not CPU time. Accepted.
- Interactive plot JSON changes slightly: the trace types and counts match, but some layout keys differ. The plots were checked against the embedded copy's output on five reference figures; a browser check of the web app was not run in this environment.
- The HOCON text the SDK writes changes its list form (one item per line). Both forms read back to the same configuration.
- Installing AMD monitoring needs the ROCm installation (`amd-smi-lib`), documented in `docs/amd-gpu-monitoring.md`.
- The ADR and the notices file are the record of these decisions; the replacement issues (cairnml#78 to #84, and #82 for plots) are the history.

## Switch-over trigger

When PyPI carries a `gpustat` release with AMD support (through `rocmi`), the AMD reader in `cairnml/utilities/gpu_query.py` is replaced by `gpustat.new_query()`, as described in `docs/amd-gpu-monitoring.md`.
