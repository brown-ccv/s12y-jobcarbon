# AGENTS.md

## What this repo is

Python tool that queries Prometheus for OSCAR Slurm job telemetry and produces a
complete [Impact Framework](https://if.greensoftware.foundation/) (`if-run`) manifest YAML It does **not** run `if-run` itself

## Source layout

The code is a proper Python package at `src/jobcarbon/` installed in editable mode via `uv` Tests import modules as `from jobcarbon.x import ...` (pytest adds `src/` to `sys.path` via `pyproject.toml`)

```
src/jobcarbon/__init__.py     # package init; exports Typer app
 src/jobcarbon/jobcarbon.py    # Typer CLI: commands manifest, batch, create-config
src/jobcarbon/config.py       # Config dataclass (loaded from jobcarbon.toml + env overrides);
                              #   also parse_sinfo / parse_hostlist for create-config
src/jobcarbon/models.py       # NodeData and Observation dataclasses
src/jobcarbon/engine.py       # Prometheus HTTP client (query, query_range, query_instant)
src/jobcarbon/loader.py       # node discovery + per-node NodeData assembly
src/jobcarbon/registry.py     # PromQL templates + NodeProfile enum + PROFILE_METRICS map
src/jobcarbon/alignment.py    # merges metric DataFrames → list[Observation]
src/jobcarbon/generator.py    # builds the final manifest dict from NodeData + plugins
src/jobcarbon/yamldump.py     # yaml.Dumper subclass that disables indentless lists
src/jobcarbon/utils.py        # shared helpers (output_text, get_config_file)
src/jobcarbon/plugins/        # one YAML plugin definition per pipeline step
```

## Developer commands

```sh
uv run pytest tests/                        # run all tests (fast, no network — uses `responses` mock)
uv run ruff check src/                      # lint
uv run ruff format src/                     # format
 uv run jobcarbon manifest $JOB_ID                # generate manifest, prints to stdout
 uv run jobcarbon manifest $JOB_ID --embodied     # include embodied carbon estimate
uv run jobcarbon batch $JOB_ID ...          # multiple jobs, one .yaml each
uv run jobcarbon create-config              # generate jobcarbon.toml from sinfo output
```

No build step required No type checker is configured

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `JOBCARBON_PROMETHEUS_URL` | `http://localhost:9390` | Base URL, no trailing slash, no `/api/v1` suffix |
| `JOBCARBON_STEP_SECONDS` | `60` | Scrape resolution; also hardcoded as `duration` in every `Observation` |
| `JOBCARBON_LOOKBACK_DAYS` | `30` | Range for initial job/node discovery query |
| `JOBCARBON_GRID_CARBON_INTENSITY` | `381` | gCO2eq/kWh; overrides `jobcarbon.toml` value |
| `JOBCARBON_CPU_LIFESPAN_YEARS` | — | Server amortisation period; overrides `jobcarbon.toml` value |
| `JOBCARBON_GPU_LIFESPAN_YEARS` | — | GPU amortisation period; overrides `jobcarbon.toml` value |
| `JOBCARBON_MAX_SAMPLES` | — | Max samples per Prometheus query chunk; overrides `jobcarbon.toml` value |

## Architecture notes

-- **Node profile selection** (`loader.py:_process_node`) — profile is inferred at runtime by probing Prometheus for `dram_power` and `gpu_power` The four profiles map 1:1 to the four template files in `templates/`
-- **Template files drive both `initialize.plugins` and `pipeline.compute`** — the generator takes the union of all plugins across all node profiles present in a job Adding a new pipeline step requires editing the template YAML, not just Python code
-- **`alignment.py` inner-joins all metric timeseries on timestamp** and raises `ValueError` on misaligned timestamps Missing metrics for a profile become `None` fields on `Observation`, not missing keys
-- **`Observation` always contains all four power fields** (`cpu_power`, `dram_power`, `host_power`, `gpu_power`) regardless of profile; unused ones are `None` and appear in the manifest inputs as `null` `if-run` passes them through harmlessly
-- **`mem_total` and `mem_allocated` are stored in GiB** — `slurm_node_mem_total` is reported by Slurm in MB; the PromQL query divides by 1024 to yield GiB `cgroup_mem_total` divides `cgroup_memory_total_bytes` by `1024 / 1024 / 1024` to also yield GiB Both fields are unit-consistent for the `mem-share` ratio and feed directly into `SciEmbodied` as `memory` (which expects GiB)
-- **`power` aggregation** — `calculate-energy` outputs a field named `power` (unit: kWh per observation interval) with `parameter-metadata` declaring `aggregation-method: {time: sum, component: sum}` Summing over time gives total job energy on that node; `carbon_operational` is derived per-timestep before aggregation so there is no double-counting
-- **`GRID_CARBON_INTENSITY = 381`** (gCO2eq/kWh, RI grid) is the default; it is read from `jobcarbon.toml` and can be overridden by `JOBCARBON_GRID_CARBON_INTENSITY`

## Testing

Tests are fully offline — all Prometheus HTTP calls are mocked with the `responses` library via fixtures in `tests/conftest.py` There are no integration tests that require a live Prometheus instance

To run a single test file:
```sh
uv run pytest tests/test_synthesis.py -v
```
