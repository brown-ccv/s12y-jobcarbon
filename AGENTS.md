# AGENTS.md

## What this repo is

Python tool that queries Prometheus for OSCAR Slurm job telemetry and produces a
complete [Impact Framework](https://if.greensoftware.foundation/) (`if-run`) manifest YAML. It does **not** run `if-run` itself.

## Source layout

All runnable code is in `src/` — there is no package subdirectory. Tests import modules directly by name (pytest is run from the repo root, which adds `src/` to `sys.path` via the `uv` editable install).

```
src/jobcarbon.py   # CLI: single job → stdout
src/batch.py       # CLI: file of job IDs → one .yml per job in outputdir
src/engine.py      # Prometheus HTTP client (query, query_range, query_instant)
src/loader.py      # node discovery + per-node NodeData assembly
src/registry.py    # PromQL templates + NodeProfile enum + PROFILE_METRICS map
src/synthesis.py   # merges metric DataFrames → list[Observation]
src/generator.py   # builds the final manifest dict from NodeData + templates
src/yamldump.py    # yaml.Dumper subclass that disables indentless lists
templates/         # one YAML pipeline template per NodeProfile value
```

## Developer commands

```sh
uv run pytest tests/           # run all tests (fast, no network — uses `responses` mock)
uv run ruff check src/         # lint
uv run ruff format src/        # format (3 files are currently unformatted)
uv run python src/jobcarbon.py $JOB_ID   # generate manifest, prints to stdout
uv run python src/batch.py jobs.txt out/ # batch mode
```

No build step required. No type checker is configured.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `JOBCARBON_PROMETHEUS_URL` | `http://localhost:9390` | Base URL, no trailing slash, no `/api/v1` suffix |
| `JOBCARBON_STEP_SECONDS` | `60` | Scrape resolution; also hardcoded as `duration` in every `Observation` |
| `JOBCARBON_LOOKBACK_DAYS` | `30` | Range for initial job/node discovery query |

## Architecture notes

- **Node profile selection** (`loader.py:_process_node`) — profile is inferred at runtime by probing Prometheus for `dram_power` and `gpu_power`. The four profiles map 1:1 to the four template files in `templates/`.
- **Template files drive both `initialize.plugins` and `pipeline.compute`** — the generator takes the union of all plugins across all node profiles present in a job. Adding a new pipeline step requires editing the template YAML, not just Python code.
- **`synthesis.py` inner-joins all metric timeseries on timestamp** and raises `ValueError` on misaligned timestamps. Missing metrics for a profile become `None` fields on `Observation`, not missing keys.
- **`Observation` always contains all four power fields** (`cpu_power`, `dram_power`, `host_power`, `gpu_power`) regardless of profile; unused ones are `None` and appear in the manifest inputs as `null`. `if-run` passes them through harmlessly.
- **`mem_total` is stored in bytes** — `loader.py` converts the raw `slurm_node_mem_total` value (reported in MB) to bytes by multiplying by `1024 * 1024`. `mem_allocated` from `cgroup_memory_total_bytes` is also in bytes, so the `mem-share` division in `host_only`/`host_only_gpu` is unit-consistent.
- **`power` aggregation** — `calculate-energy` outputs a field named `power` (unit: kWh per observation interval) with `parameter-metadata` declaring `aggregation-method: {time: avg, component: sum}`. Averaging over time is correct for a per-interval energy value (i.e. a rate); summing would overcount. The `sci-o` plugin multiplies `grid_carbon_intensity × power`.
- **`GRID_CARBON_INTENSITY = 381`** (gCO2eq/kWh, RI grid) is hardcoded in both `jobcarbon.py` and `batch.py`, not read from config.

## Testing

Tests are fully offline — all Prometheus HTTP calls are mocked with the `responses` library via fixtures in `tests/conftest.py`. There are no integration tests that require a live Prometheus instance.

To run a single test file:
```sh
uv run pytest tests/test_synthesis.py -v
```
