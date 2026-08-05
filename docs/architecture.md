# Architecture & source layout

The code is a proper Python package at `src/jobcarbon/`, installed editable via
`uv`. Tests import modules as `from jobcarbon.x import ...` (pytest adds `src/`
to `sys.path` via `pyproject.toml`).

```
src/jobcarbon/__init__.py         # package init; exports Typer app
src/jobcarbon/jobcarbon.py        # Typer CLI: commands `manifest`, `batch`
src/jobcarbon/config.py           # Config dataclass (jobcarbon.toml + env overrides)
src/jobcarbon/models.py           # NodeData and Observation dataclasses
src/jobcarbon/engine.py           # Prometheus HTTP client (query, query_range, query_instant)
src/jobcarbon/loader.py           # node discovery + per-node NodeData assembly
src/jobcarbon/registry.py         # PromQL metric definitions (MetricDefinition)
src/jobcarbon/alignment.py        # merges metric DataFrames -> list[Observation]
src/jobcarbon/generator.py        # builds the final manifest dict from NodeData + plugins
src/jobcarbon/electricity_maps.py # grid carbon-intensity lookup
src/jobcarbon/validate.py         # manifest validation
src/jobcarbon/yamldump.py         # yaml.Dumper subclass that disables indentless lists
src/jobcarbon/utils.py            # shared helpers (output_text, get_config_file)
src/jobcarbon/plugins/            # one YAML plugin definition per pipeline step
```

## Notes

- **Node profile selection** (`loader.py:_process_node`) — profile is inferred
  at runtime by probing Prometheus for `dram_power` and `gpu_power`.

- **Plugin YAMLs drive both `initialize.plugins` and `pipeline.compute`** — the
  generator takes the union of all plugins across all node profiles present in a
  job. Adding a new pipeline step means adding/editing a plugin YAML in
  `src/jobcarbon/plugins/`, not just Python code.

- **`alignment.py` inner-joins all metric timeseries on timestamp** and raises
  `ValueError` on misaligned timestamps. Missing metrics for a profile become
  `None` fields on `Observation`, not missing keys.

- **`Observation` always contains all four power fields** (`cpu_power`,
  `dram_power`, `host_power`, `gpu_power`) regardless of profile; unused ones are
  `None` and appear in manifest inputs as `null`. `if-run` passes them through
  harmlessly.

- **`mem_total` and `mem_allocated` are stored in GiB** — `slurm_node_mem_total`
  is reported by Slurm in MB, so the PromQL query divides by 1024 to yield GiB;
  `cgroup_mem_total` divides `cgroup_memory_total_bytes` by 1024^3. Both feed the
  `mem-share` ratio and go directly into `SciEmbodied` as `memory` (expects GiB).

- **`energy` aggregation** — `calculate-energy` outputs `energy` (kWh per
  observation interval) with `parameter-metadata` declaring
  `aggregation-method: {time: sum, component: sum}`. Summing over time gives total
  job energy on that node; `carbon_operational` is derived per-timestep before
  aggregation, so there is no double-counting.

- **`GRID_CARBON_INTENSITY = 381`** (gCO2eq/kWh, RI grid) is the default; read
  from `jobcarbon.toml`, overridable by `JOBCARBON_GRID_CARBON_INTENSITY`.
