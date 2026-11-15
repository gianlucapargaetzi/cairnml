# AMD GPU monitoring

The resource monitor reports GPU utilisation, memory and power for NVIDIA GPUs through the extra `cairnml[gpu]`, which
installs NVIDIA's bindings and the `gpustat` package.

AMD GPUs are read through AMD's own Python API, `amdsmi`. It is **not** a pip dependency of cairnml: it comes with the
ROCm installation on the host, and the monitor imports it only when the host has the ROCm kernel driver (`/dev/kfd`).

To enable AMD monitoring:

1. Install the ROCm SMI library from your distribution's ROCm packages (for example `apt install amd-smi-lib`).
2. Install the Python package from the ROCm share folder, as in AMD's installation guide for `amdsmi`.
3. Check that the monitor can read the GPUs:

   ```sh
   python -c "from cairnml.utilities.gpu_query import query; print(query().jsonify())"
   ```

Without `amdsmi` the monitor reports that GPU monitoring is not available. A value that AMD's API cannot read is left
empty in the task's statistics rather than reported as a fake value.

**Status:** the AMD reader is written against AMD's documented `amdsmi` API and tested with a stand-in module. It has not
yet been run on AMD hardware.

## Switching to upstream gpustat's AMD support

When a `gpustat` release with AMD support (through `rocmi`) is on PyPI, the AMD reader can be replaced by it:

1. Raise the `gpu` extra to that release.
2. Replace the reader in `cairnml/utilities/gpu_query.py` by `gpustat.new_query()`, and delete the AMD tests.
3. Keep `driver_cuda_version()`: upstream does not report the CUDA driver version.
4. Check on an AMD host that unified-memory devices (whose total memory may be unknown) are handled.
