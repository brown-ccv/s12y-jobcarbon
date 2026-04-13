# Job Carbon Overhaul Plan

## Goal
Transform `jobcarbon` from a rigid estimation script into a flexible, profile-aware manifest
generator prioritizing actual power measurements over estimates.

## Core Architecture
Unidirectional data flow:
`Registry` → `Engine` → `Builder` → `Zipper` → `Generator`

---

## Real Metric Names (verified against localhost:9390)

| Intent | Metric | Labels of note |
|---|---|---|
| CPU pkg power per socket | `scaph_domain_power_microwatts{domain_name="core"}` | `socket_id`, `instance=host:9191` |
| DRAM power per socket | `scaph_domain_power_microwatts{domain_name="dram"}` | `socket_id`, `instance=host:9191` |
| Host aggregate power | `scaph_host_power_microwatts` | `instance=host:9191` |
| GPU power (job-attributed) | `nvidia_gpu_power_usage_milliwatts` | `jobid`, `minor_number`, `instance=host:9445` |
| Node total CPUs | `slurm_node_cpu_total` | `node=hostname` — constant, verified == node_exporter count |
| Node total memory | `slurm_node_mem_total` | `node=hostname` — constant |
| Cgroup observations | `cgroup_cpu_total_seconds` | `jobid`, `instance=host:9306`, optional `step`, `task` |

### Instance label formats (four exporters)
- Scaphandre: `hostname:9191`
- Cgroup exporter: `hostname:9306`
- NVIDIA exporter: `hostname:9445`
- Slurm exporter: bare `node=hostname` label, no port

Scaph/cgroup/GPU queries use `instance=~'{node}:.*'`. Slurm queries use `node='{node}'`.
Registry template strings encode this — callers don't need to know.

### Discovery
`engine.query()` (instant query with range vector) on `job_cgroup` returns all raw samples for
the job across all nodes. First/last sample timestamps give the job window to ~scrape-interval
accuracy (~30s). Results present → nodes + window derived. No results → job not found in
lookback window.

Profile detection: query `dram_power` via `query_range` over the derived window.
Results present → `FULL`. Empty → `HOST_ONLY`. No Scaphandre data at all → skip node with error.

### Array jobs
Slurm records each array task as a distinct `jobid` (e.g. `12345_1`, `12345_2`). The cgroup
exporter tracks them separately. Each task is processed independently — array jobs are handled
correctly as long as the input TSV contains one row per task (which `sacct` produces).

### Multi-node jobs
`job_cgroup{jobid=X}` returns one series per node — the node list and window are both derived
from this single query. No separate node lookup needed.

### Step and task rows
Cgroup metrics include sub-cgroup rows with `step` and `task` labels. These are preserved in
observations — they allow decomposition of user work vs. system overhead and array task
structure. `job_cgroup` bakes `step='',task=''` into the PromQL to return only top-level rows
for discovery. `cgroup_window` returns all rows for per-node observations.

---

## Abstractions

### 1. Metric Registry
All PromQL lives here. `cpu_power` and `dram_power` both query `scaph_domain_power_microwatts`
with different `domain_name` filters baked into the template string.

```python
@dataclass
class MetricDefinition:
    id: str
    query: str  # PromQL template string, parameters: {node}, {jobid}
    unit: str

METRIC_REGISTRY = {
    "cpu_power": MetricDefinition(
        id="cpu_power",
        query="scaph_domain_power_microwatts{{domain_name='core',instance=~'{node}:.*'}}",
        unit="microwatts",
    ),
    "dram_power": MetricDefinition(
        id="dram_power",
        query="scaph_domain_power_microwatts{{domain_name='dram',instance=~'{node}:.*'}}",
        unit="microwatts",
    ),
    "host_power": MetricDefinition(
        id="host_power",
        query="scaph_host_power_microwatts{{instance=~'{node}:.*'}}",
        unit="microwatts",
    ),
    "gpu_power": MetricDefinition(
        id="gpu_power",
        query="nvidia_gpu_power_usage_milliwatts{{instance=~'{node}:.*',jobid='{jobid}'}}",
        unit="milliwatts",
    ),
    "node_cpu_total": MetricDefinition(
        id="node_cpu_total",
        query="slurm_node_cpu_total{{node='{node}'}}",
        unit="cores",
    ),
    "node_mem_total": MetricDefinition(
        id="node_mem_total",
        query="slurm_node_mem_total{{node='{node}'}}",
        unit="megabytes",
    ),
    # engine.query() only — no instance filter, bakes step/task filters in to return
    # one series per node. Used to discover nodes and derive the job window.
    "job_cgroup": MetricDefinition(
        id="job_cgroup",
        query="cgroup_cpu_total_seconds{{jobid='{jobid}',step='',task=''}}",
        unit="seconds",
    ),
    "cgroup_window": MetricDefinition(
        id="cgroup_window",
        query="cgroup_cpu_total_seconds{{instance=~'{node}:.*',jobid='{jobid}'}}",
        unit="seconds",
    ),
}
```

### 2. Profile → Metrics Mapping
`gpu_power` is in both profiles — an empty result simply means no GPUs were assigned to
this job on this node. `dram_power` is fetched during profile detection and reused, not
re-fetched. `node_cpu_total` and `node_mem_total` are fetched separately and stored as
integer scalars on `NodeData`.

```python
class NodeProfile(Enum):
    FULL      = "full"       # CPU pkg + DRAM power reported per socket
    HOST_ONLY = "host_only"  # aggregate host power only, no DRAM breakdown

PROFILE_METRICS = {
    NodeProfile.FULL:      ["cpu_power", "dram_power", "gpu_power"],
    NodeProfile.HOST_ONLY: ["host_power", "gpu_power"],
}
```

### 3. Prometheus Engine
Two methods. `query_range` for time series observations; `query` for instant queries with a
range vector selector (window discovery only). Both return the raw Prometheus result list —
shape transformation belongs to the zipper.

```python
STEP_SECONDS = int(os.environ.get("JOBCARBON_STEP_SECONDS", 60))
LOOKBACK_DAYS = int(os.environ.get("JOBCARBON_LOOKBACK_DAYS", 30))

class PrometheusEngine:
    def query_range(self, metric, window, node="", jobid="", step_seconds=None) -> list[dict]:
        # Returns: [{"metric": {...labels}, "values": [[ts, val], ...]}, ...]

    def query(self, metric, node="", jobid="", lookback_days=LOOKBACK_DAYS) -> list[dict]:
        # Instant query with range vector: metric.query[Nd]
        # Returns matrix resultType — same shape as query_range.
        # Used only for job_cgroup window discovery.
```

### 4. Loader
Node list and job window derived from a single `engine.query()` call on `job_cgroup`.
Profile detection inlined into `_process_node` — queries `dram_power`, reuses result.
Capacity scalars extracted as integers and stored on `NodeData`.
Single public entry point: `process_job()`.

```python
@dataclass
class NodeData:
    node: str
    profile: NodeProfile
    metrics: dict[str, list[dict]]   # raw Prometheus result lists, keyed by metric id
    cpu_total: int                   # total cores on this node
    mem_total: int                   # total memory in MB on this node

def process_job(engine, jobid, lookback_days=LOOKBACK_DAYS) -> list[NodeData]:
    nodes, window = _get_nodes(engine, jobid, lookback_days)
    return [_process_node(engine, node, jobid, window) for node in nodes]

def _get_nodes(engine, jobid, lookback_days) -> tuple[list[str], Window]:
    results = engine.query(METRIC_REGISTRY["job_cgroup"], jobid=jobid, lookback_days=lookback_days)
    # one series per node (step/task already filtered in PromQL)
    ...

def _process_node(engine, node, jobid, window) -> NodeData:
    dram_results = engine.query_range(METRIC_REGISTRY["dram_power"], window, node=node)
    profile = NodeProfile.FULL if dram_results else NodeProfile.HOST_ONLY
    metrics = {mid: engine.query_range(...) for mid in PROFILE_METRICS[profile]}
    if profile == NodeProfile.FULL:
        metrics["dram_power"] = dram_results  # reuse, don't re-fetch
    ...
```

### 5. Observation Zipper
Aggregates multi-series metrics (multiple sockets, multiple GPUs) then inner-joins all
metrics on timestamp.

- `cpu_power`: sum across `socket_id` → total node CPU pkg power
- `dram_power`: sum across `socket_id` → total node DRAM power
- `gpu_power`: sum across `minor_number` → total node GPU power for this job

Column names in the output must match parameter names in the YAML fragment for that profile.

### 6. Manifest Generator
The observation tree is **built entirely by the generator** — the base template holds only
static job-level metadata. One child node per entry in `node_data`.

```
templates/
  base.yaml       # static: name, description, aggregation config only
fragments/
  full.yaml       # IF pipeline: cpu_power + dram_power + gpu_power → watts → energy → carbon
  host_only.yaml  # IF pipeline: scale host_power by reservation share → energy → carbon
```

```python
def generate_manifest(job_id, node_data, defaults):
    manifest = yaml.safe_load(open("templates/base.yaml"))
    manifest["name"] = f"job{job_id}"
    manifest["tree"] = {"children": {}}

    for node, data in node_data.items():
        fragment = yaml.safe_load(open(f"fragments/{data.profile.value}.yaml"))
        manifest["tree"]["children"][node] = {
            "pipeline": {"compute": fragment},
            "defaults": defaults[node],
            "inputs": zip_observations(data.metrics_map, node),
        }
    return yaml.dump(manifest)
```

The base template never encodes node names, profiles, or tree structure.

---

## Scaling Logic (`HOST_ONLY`)

$$\text{Job Power} = \text{Host Power} \times \left( w_{cpu} \cdot \frac{\text{CPU}_{\text{res}}}{\text{CPU}_{\text{cap}}} + w_{mem} \cdot \frac{\text{Mem}_{\text{res}}}{\text{Mem}_{\text{cap}}} \right)$$

- Uses **reservation** not utilization — a reserved-but-idle resource still displaces host power.
- `cpu_res` / `mem_res` from Slurm TRES allocation, passed as `defaults` into the manifest.
- `cpu_total` / `mem_total` from Prometheus, stored as scalars on `NodeData`.
- CPU/DRAM power in **microwatts**, GPU power in **milliwatts** — fragments handle conversion.

---

## Repo Structure

### Current
```
src/
  jobcarbon.py    # single-job CLI + all query logic
  batch.py        # batch CLI + manifest template as Python dict
  yamldump.py     # yaml helper
```

### Target
```
src/
  registry.py     # MetricDefinition, METRIC_REGISTRY, NodeProfile, PROFILE_METRICS  ✓
  engine.py       # PrometheusEngine: query_range() + query()  ✓
  loader.py       # NodeData, process_job() + private helpers  ✓
  zipper.py       # zip_observations()
  generator.py    # generate_manifest(), load_fragment()
  yamldump.py     # unchanged
  batch.py        # rewired; keeps CLI interface
  jobcarbon.py    # rewired; keeps CLI interface
templates/        # ✓
  base.yaml
fragments/        # ✓
  full.yaml
  host_only.yaml
```

### `pyproject.toml` changes
```toml
[project.scripts]
jobcarbon = "jobcarbon:main"
batch = "batch:main"
```

---

## Task List

### Setup
- [x] **Task 1** — Create `templates/` and `fragments/` directories

### Core modules (implement in order)
- [x] **Task 2** — `registry.py`: `MetricDefinition`, `METRIC_REGISTRY`, `NodeProfile`, `PROFILE_METRICS`
- [x] **Task 3** — `engine.py`: `PrometheusEngine` with `query_range()` and `query()`
- [x] **Task 4** — `loader.py`: `NodeData`, `process_job()`, `_get_nodes()`, `_process_node()`
- [ ] **Task 5** — `zipper.py`: `zip_observations()` — aggregate multi-series, inner-join on timestamp

### Templating
- [ ] **Task 6** — `templates/base.yaml`: static job-level metadata only
- [ ] **Task 7** — `fragments/full.yaml`: cpu_power + dram_power + gpu_power → watts → energy → carbon
- [ ] **Task 8** — `fragments/host_only.yaml`: scale host_power by reservation share → energy → carbon

### Assembly & wiring
- [ ] **Task 9** — `generator.py`: `generate_manifest()` builds tree dynamically; injects fragment + observations per node
- [ ] **Task 10** — Rewire `jobcarbon.py`; remove old query functions
- [ ] **Task 11** — Rewire `batch.py`; remove `job_yaml_template`; add `batch` entry point to `pyproject.toml`
