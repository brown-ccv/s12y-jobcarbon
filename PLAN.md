# Job Carbon Overhaul Plan

## Goal
Transform `jobcarbon` from a rigid estimation script into a flexible, profile-aware manifest
generator prioritizing actual power measurements over estimates.

## Core Architecture
Unidirectional data flow:
`Registry` → `Engine` → `Orchestrator` → `Zipper` → `Generator`

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
| Cgroup window + node probe | `cgroup_cpu_total_seconds` | `jobid`, `instance=host:9306`, optional `step`, `task` |

### Instance label formats (four exporters)
- Scaphandre: `hostname:9191`
- Cgroup exporter: `hostname:9306`
- NVIDIA exporter: `hostname:9445`
- Slurm exporter: bare `node=hostname` label, no port

Scaph/cgroup/GPU queries use `instance=~'{node}:.*'`. Slurm queries use `node='{node}'`.
Registry template strings encode this — callers don't need to know.

### Discovery
Query `dram_power` as a range over the job window. Results present → `FULL`. No results →
`HOST_ONLY`. No Scaphandre data at all → skip node with error.

### Array jobs
Slurm records each array task as a distinct `jobid` (e.g. `12345_1`, `12345_2`). The cgroup
exporter tracks them separately. Each task is processed independently — array jobs are handled
correctly as long as the input TSV contains one row per task (which `sacct` produces).

### Multi-node jobs
`cgroup_cpu_total_seconds{jobid=X}` returns one series per node — the node list is a
byproduct of the window probe query. No separate node lookup needed.

### Step and task rows
Cgroup metrics include sub-cgroup rows with `step` and `task` labels. These are preserved in
observations — they allow decomposition of user work vs. system overhead and array task
structure. Only top-level rows (no `step`, no `task`) are used for node discovery and window
extraction.

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

# Nodes without Scaphandre data are skipped — no estimation fallback.
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
    "node_cpu_cap": MetricDefinition(
        id="node_cpu_cap",
        query="slurm_node_cpu_total{{node='{node}'}}",
        unit="cores",
    ),
    "node_mem_cap": MetricDefinition(
        id="node_mem_cap",
        query="slurm_node_mem_total{{node='{node}'}}",
        unit="megabytes",
    ),
    # Cgroup lifetime probe — top-level rows (no step/task) used for node
    # discovery and window extraction. All rows passed to zipper.
    "cgroup_window": MetricDefinition(
        id="cgroup_window",
        query="cgroup_cpu_total_seconds{{instance=~'{node}:.*',jobid='{jobid}'}}",
        unit="seconds",
    ),
}
```

### 2. Profile → Metrics Mapping
`gpu_power` is in both profiles — an empty result simply means no GPUs were assigned to
this job on this node. `node_cpu_cap` is in `FULL` for per-core power normalisation.

```python
class NodeProfile(Enum):
    FULL      = "full"       # CPU pkg + DRAM power reported per socket
    HOST_ONLY = "host_only"  # aggregate host power only, no DRAM breakdown

PROFILE_METRICS = {
    NodeProfile.FULL:      ["cpu_power", "dram_power", "gpu_power", "node_cpu_cap"],
    NodeProfile.HOST_ONLY: ["host_power", "gpu_power", "node_cpu_cap", "node_mem_cap"],
}
```

### 3. Prometheus Engine
Single method. Returns raw Prometheus result list — shape transformation belongs to the zipper.

```python
class PrometheusEngine:
    def query_range(self, metric_def, window, node=None, jobid=None) -> list[dict]:
        q = metric_def.query.format(node=node, jobid=jobid)
        # Returns: [{"metric": {...labels}, "values": [[ts, val], ...]}, ...]
        return self.client.get_range(q, window.start, window.end, step=STEP_SECONDS)
```

### 4. Orchestrator
Node list and job window come from the same `cgroup_window` query. Discovery (`dram_power`)
uses the same engine call as all other metrics.

```python
def get_nodes_and_window(engine, job_id, slurm_window=None):
    # Query without node filter to get all nodes for this job
    results = engine.query_range(METRIC_REGISTRY["cgroup_window"], slurm_window, jobid=job_id)
    # Top-level rows only (no step, no task) → one per node
    top_level = [r for r in results if "step" not in r["metric"] and "task" not in r["metric"]]
    nodes = list({r["metric"]["instance"].split(":")[0] for r in top_level})
    window = slurm_window or Window(
        start=min(v[0] for r in top_level for v in r["values"]),
        end=max(v[0] for r in top_level for v in r["values"]),
    )
    return nodes, window

def determine_profile(dram_results) -> NodeProfile:
    if dram_results:
        return NodeProfile.FULL
    return NodeProfile.HOST_ONLY

def process_node(node, job_id, engine, window):
    dram_results = engine.query_range(METRIC_REGISTRY["dram_power"], window, node=node)
    profile = determine_profile(dram_results)

    data_map = {
        mid: engine.query_range(METRIC_REGISTRY[mid], window, node=node, jobid=job_id)
        for mid in PROFILE_METRICS[profile]
    }
    if profile == NodeProfile.FULL:
        data_map["dram_power"] = dram_results  # reuse, don't re-fetch

    return profile, data_map
```

### 5. Observation Zipper
Aggregates multi-series metrics (multiple sockets, multiple GPUs) then inner-joins all
metrics on timestamp.

- `cpu_power`: sum across `socket_id` → total node CPU pkg power
- `dram_power`: sum across `socket_id` → total node DRAM power
- `gpu_power`: sum across `minor_number` → total node GPU power for this job
- `node_cpu_cap`, `node_mem_cap`: constant — take first value

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
- `node_cpu_cap` / `node_mem_cap` from Prometheus, fetched as part of both profiles.
- CPU power in **microwatts**, GPU power in **milliwatts** — fragments handle conversion.

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
  engine.py       # PrometheusEngine (query_range only)
  orchestrator.py # get_nodes_and_window(), determine_profile(), process_node()
  zipper.py       # zip_observations()
  generator.py    # generate_manifest(), load_fragment()
  yamldump.py     # unchanged
  batch.py        # rewired; keeps CLI interface
  jobcarbon.py    # rewired; keeps CLI interface
templates/
  base.yaml       # static job-level metadata only
fragments/
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
- [ ] **Task 1** — Create `templates/` and `fragments/` directories

### Core modules (implement in order)
- [x] **Task 2** — `registry.py`: `MetricDefinition`, `METRIC_REGISTRY`, `NodeProfile`, `PROFILE_METRICS`
- [ ] **Task 3** — `engine.py`: `PrometheusEngine` with `query_range()` only
- [ ] **Task 4** — `orchestrator.py`: `get_nodes_and_window()`, `determine_profile()`, `process_node()`
- [ ] **Task 5** — `zipper.py`: `zip_observations()` — aggregate multi-series, inner-join on timestamp

### Templating
- [ ] **Task 6** — `templates/base.yaml`: static job-level metadata only
- [ ] **Task 7** — `fragments/full.yaml`: cpu_power + dram_power + gpu_power → watts → energy → carbon
- [ ] **Task 8** — `fragments/host_only.yaml`: scale host_power by reservation share → energy → carbon

### Assembly & wiring
- [ ] **Task 9** — `generator.py`: `generate_manifest()` builds tree dynamically; injects fragment + observations per node
- [ ] **Task 10** — Rewire `jobcarbon.py`; remove old query functions
- [ ] **Task 11** — Rewire `batch.py`; remove `job_yaml_template`; add `batch` entry point to `pyproject.toml`
