# Job Carbon

`jobcarbon` estimates the carbon footprint of Slurm jobs. It queries Prometheus for power telemetry (Scaphandre CPU/DRAM, NVIDIA GPU) and job resource allocation, then produces an [Impact Framework](https://if.greensoftware.foundation/) manifest ready to be evaluated by `if-run`. Both operational carbon (from energy use) and embodied carbon (from hardware manufacture) are computed per node and summed.

## Commands

```sh
jobcarbon manifest JOB_ID [--embodied] [--output FILE]    # one job → IF manifest (stdout by default)
jobcarbon batch JOB_ID... [--embodied] [--output-dir DIR] # many jobs → one job<ID>.yaml each
jobcarbon embodied HOSTLIST...                            # embodied carbon of node hardware, no job needed
jobcarbon validate-config                                 # check the resolved config loads (offline)
```

Job time windows and nodes are discovered automatically from Prometheus cgroup data — no timestamps needed. Hostlists accept Slurm syntax (`gpu[4001-4008]`).

```sh
jobcarbon manifest 4690148 --embodied > manifest.yaml
if-run -m manifest.yaml -o output
```

## Configuration

A `config.toml` (hardware inventory + settings) must be found in one of, in order: `$JOBCARBON_CONFIG`, `$XDG_CONFIG_HOME/jobcarbon/config.toml`, the package, or `/etc/jobcarbon/config.toml`. Any value can be overridden by a `JOBCARBON_<KEY>` env var:

| Variable                   | Default                 | Description                             |
| -------------------------- | ----------------------- | --------------------------------------- |
| `JOBCARBON_PROMETHEUS_URL` | `http://localhost:9390` | Prometheus base URL                     |
| `JOBCARBON_STEP_SECONDS`   | `60`                    | Time-series resolution in seconds       |
| `JOBCARBON_LOOKBACK_DAYS`  | `30`                    | How far back to search for a job's data |

See `METHODOLOGY.md` for the carbon model and `docs/die-areas.md` for hardware sources.

## Development

Requires Python ≥ 3.12 and [`uv`](https://docs.astral.sh/uv/) (`pip install uv`).

```sh
git clone <repo> && cd s12y-jobcarbon
uv sync              # install deps into a local venv
JOBCARBON_CONFIG=config/config.toml uv run jobcarbon validate-config

uv run pytest        # tests
uv run ruff check    # lint
uv run ruff format   # format
uv run pyright       # typecheck
```
