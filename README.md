# Job Carbon

`jobcarbon` estimates the carbon footprint of OSCAR Slurm jobs. It queries Prometheus
for power telemetry (Scaphandre CPU/DRAM, NVIDIA GPU) and job resource allocation data,
then produces a complete [Impact Framework](https://if.greensoftware.foundation/) manifest
ready to be evaluated by `if-run`.

## Prerequisites

`jobcarbon` uses `uv` to manage dependencies. To install `uv` refer to the
[`uv` documentation](https://docs.astral.sh/uv/), or run:

```sh
pip install uv
```

A Prometheus instance must be reachable. By default `jobcarbon` connects to
`http://localhost:9390`. Override this with the `JOBCARBON_PROMETHEUS_URL` environment
variable:

```sh
export JOBCARBON_PROMETHEUS_URL=http://localhost:9390
```

Two other optional environment variables control query behaviour:

| Variable | Default | Description |
|---|---|---|
| `JOBCARBON_STEP_SECONDS` | `60` | Time-series resolution in seconds |
| `JOBCARBON_LOOKBACK_DAYS` | `30` | How far back to search for a job's data |

## Running

Pass a Slurm job ID. The tool discovers the job's time window and nodes automatically
from Prometheus cgroup data — no start/end timestamps are required.

```sh
uv run python src/jobcarbon.py $JOB_ID
```

The output is a complete Impact Framework manifest printed to stdout. Redirect it to a
file and pass it to `if-run`:

```sh
uv run python src/jobcarbon.py $JOB_ID > manifest.yaml
if-run -m manifest.yaml -o output
```

### Example

```sh
$ uv run python src/jobcarbon.py 1667979 > manifest.yaml
$ if-run -m manifest.yaml -o output
$ head output.yaml
aggregation:
  metrics:
    - duration
    - energy
    - carbon_operational
    - carbon_embodied
    - carbon
  type: both
...
tree:
  children:
    node1648:
      aggregated:
        carbon_operational: 2650.032
        carbon_embodied: 2532.548
        carbon: 5182.580
      ...
```

The manifest contains one child per compute node. Each node's pipeline is selected
automatically based on available telemetry:

| Profile | Condition | Pipeline |
|---|---|---|
| `full` | Scaphandre CPU + DRAM data present | CPU + DRAM power → energy → carbon |
| `full_gpu` | Scaphandre CPU + DRAM + GPU data present | CPU + DRAM + GPU power → energy → carbon |
| `host_only` | Only whole-host Scaphandre power available | Host power scaled by reservation share → energy → carbon |
| `host_only_gpu` | Whole-host power + GPU data | Host power (scaled) + GPU power → energy → carbon |

Carbon is reported in gCO2eq using a grid carbon intensity of **381 gCO2eq/kWh**
(Rhode Island grid average). Both operational carbon (from energy use) and embodied
carbon (from hardware manufacture, via `SciEmbodied`) are computed and summed.

## Batch mode

`batch` generates manifests for a list of job IDs read from a plain text file (one ID
per line) and writes one `.yml` file per job into an output directory:

```sh
uv run python src/batch.py jobs.txt output/
```

Jobs that fail (e.g. no Prometheus data found within the lookback window) are reported
and skipped; processing continues for the remaining jobs.
