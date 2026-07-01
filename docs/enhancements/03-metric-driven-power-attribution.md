---
title: Metric-Driven Per-Component Power Attribution
status: proposed
owners: [@broarr]
created: 2026-07-01
updated: 2026-07-01
---

# Metric-Driven Per-Component Power Attribution (PRD)

Goal: replace the profile-enum-based pipeline with a metric-driven model where each available
Scaphandre power domain is scaled by its respective resource reservation share before summing
to node power. Remove `scaph_host_power_microwatts` from all pipelines entirely.

## Summary

- Remove the `NodeProfile` enum and all profile-based dispatch.
- Replace it with boolean flags (`has_dram`, `has_gpu`) on `NodeData`, discovered at runtime by
  probing Prometheus.
- Scale each component metric by its resource reservation share:
  - `cpu_power × cpu_share → node_power_kw` (cpu-only) or `→ attributed_cpu_power` (cpu+dram)
  - `dram_power × mem_share → attributed_dram_power` (when DRAM metric present)
  - `gpu_power` is already job-filtered by PromQL; sum directly (when GPU metric present)
- `node_power_kw = sum of all attributed values present`
- `host_power` (`scaph_host_power_microwatts`) is never queried or used.

## Motivation

### The `FULL` profile is wrong

The current `FULL` pipeline sums `cpu_power + dram_power` with no scaling:

```yaml
# sum-scaph-power.yaml (current)
method: Sum
config:
  input-parameters: [cpu_power, dram_power]
  output-parameter: node_power_kw
```

`scaph_socket_power_microwatts` reports whole-socket power, not the job's share. On a
shared node running multiple jobs simultaneously, this attributes 100% of socket power to
every job queried, producing a substantial overcount.

### The `HOST_ONLY` profile is wrong for different reasons

`scaph_host_power_microwatts` is the sum of all power domains on the node. Scaling it by a
weighted 70/30 cpu/mem reservation share compounds two errors:

1. The 70/30 split was never empirically validated (METHODOLOGY.md §4 flags this explicitly).
2. Applying a memory-share weight implies DRAM is a separable, independently-scalable
   component of host power — which it is not in this context.

### The correct model

Each Scaphandre power domain metric reports whole-component power. Each should be scaled
independently by the job's share of the resource that domain measures:

- `scaph_socket_power_microwatts` → scale by `cpu_allocated / cpu_total`
- `scaph_domain_power_microwatts{domain_name="dram"}` → scale by
  `mem_allocated / mem_total`

GPU power from the NVIDIA SMI exporter is already filtered to the job's assigned GPUs via the
`jobid` label in PromQL; no further scaling is needed. On Oscar's cluster MIG is not used, so
each GPU is either fully assigned to a job or not assigned at all.

### Empirical basis

Checks against Oscar's hardware confirm:
- All 104 nodes export `scaph_socket_power_microwatts`. No node lacks CPU domain data.
- A subset of nodes also export `scaph_domain_power_microwatts{domain_name="dram"}`.
- No node exports DRAM domain data without also exporting CPU domain data.
- `scaph_host_power_microwatts` is available on some nodes but carries no attribution
  information that cannot be derived more accurately from per-domain metrics.

## Scope

- Remove `NodeProfile` enum from `registry.py`.
- Remove `GPU_PROFILES`, `HOST_PROFILES` sets from `registry.py`.
- Remove `host_power` from `METRIC_REGISTRY` in `registry.py`.
- Add `has_dram: bool` and `has_gpu: bool` to `NodeData` in `models.py`.
- Rewrite `_process_node` in `loader.py` to set flags instead of assigning a profile; remove
  `host_power` query; raise `ValueError` when no CPU power timeseries is returned.
- Rewrite pipeline dispatch in `generator.py` to build the step list dynamically from flags.
- Add new attribution plugins; delete obsolete ones.
- Update `METHODOLOGY.md` §2 and §4.

## Out of Scope

- MIG (multi-instance GPU) partitioning — Oscar does not use MIG.
- Any change to embodied carbon pipelines other than replacing profile guards with flag guards.
- Changes to the Prometheus metric set or scrape configuration.

## Design Decisions

### No profile enum — boolean flags on `NodeData`

A `NodeProfile` enum made sense when the pipeline was static per profile. With dynamic
pipeline assembly the enum adds indirection without value. Two boolean flags (`has_dram`,
`has_gpu`) carry exactly the information the generator needs and are directly derived from
Prometheus probe results.

`_pipeline_steps`, `_embodied_steps`, and `_gpu_defaults` gate on these flags instead of
`profile in GPU_PROFILES` etc.

### `host_power` is never used

`scaph_host_power_microwatts` is removed from `METRIC_REGISTRY` and never queried. All
power attribution flows through per-domain metrics.

### `cpu_power` absence is a hard error

All Oscar nodes have `scaph_socket_power_microwatts`. If a node is discovered (via cgroup
data) but returns no CPU power timeseries, `_process_node` raises `ValueError`. This is
preferable to silently falling back to host power.

### GPU power needs no scaling

The PromQL query for `gpu_power` filters by `jobid` label and sums across all GPUs assigned
to the job on that node. On a cluster without MIG, each GPU is either fully assigned to a job
or not assigned at all. The sum of filtered GPU watts is the job's full GPU power draw.

### `scale-cpu-power` outputs different fields depending on DRAM presence

When DRAM is absent, `scale-cpu-power` outputs `node_power_kw` directly — no sum step
needed. When DRAM is present, `scale-cpu-power-dram` outputs `attributed_cpu_power`, which
is then summed with `attributed_dram_power` by `sum-attributed-power-dram`.

This requires two scale-cpu plugins with different output field names. The generator picks
based on `has_dram`. This avoids the need for a single-input `Sum` passthrough step (whose
behaviour in IF is unconfirmed).

### `OPERATIONAL_DEFAULTS` — inject all four allocation fields for all nodes

`cpu_total`, `cpu_allocated`, `mem_total`, `mem_allocated` are injected into `defaults` for
all nodes because `mem_allocated` and `mem_total` are always needed for embodied carbon
regardless of `has_dram`. This is unchanged from current behaviour for host-only profiles,
and extends it to all nodes.

## Pipeline Shapes

### CPU-only (no DRAM, no GPU)

```
cpu-share                   Divide: cpu_allocated / cpu_total → cpu_share
scale-cpu-power             Multiply: cpu_power × cpu_share → node_power_kw
duration-to-hours
calculate-energy
calculate-carbon-operational
```

### CPU + DRAM (no GPU)

```
cpu-share                   Divide: cpu_allocated / cpu_total → cpu_share
scale-cpu-power-dram        Multiply: cpu_power × cpu_share → attributed_cpu_power
mem-share                   Divide: mem_allocated / mem_total → mem_share
scale-dram-power            Multiply: dram_power × mem_share → attributed_dram_power
sum-attributed-power-dram   Sum: [attributed_cpu_power, attributed_dram_power] → node_power_kw
duration-to-hours
calculate-energy
calculate-carbon-operational
```

### CPU-only + GPU

```
cpu-share
scale-cpu-power             Multiply: cpu_power × cpu_share → node_power_kw
sum-gpu-power               Sum: [node_power_kw, gpu_power] → node_power_kw
duration-to-hours
calculate-energy
calculate-carbon-operational
```

### CPU + DRAM + GPU

```
cpu-share
scale-cpu-power-dram        Multiply: cpu_power × cpu_share → attributed_cpu_power
mem-share
scale-dram-power            Multiply: dram_power × mem_share → attributed_dram_power
sum-attributed-power-dram   Sum: [attributed_cpu_power, attributed_dram_power] → node_power_kw
sum-gpu-power               Sum: [node_power_kw, gpu_power] → node_power_kw
duration-to-hours
calculate-energy
calculate-carbon-operational
```

## `_operational_steps` logic

```python
def _operational_steps(node_data: NodeData) -> list[str]:
    if node_data.has_dram:
        steps = [
            "cpu-share", "scale-cpu-power-dram",
            "mem-share", "scale-dram-power",
            "sum-attributed-power-dram",
        ]
    else:
        steps = ["cpu-share", "scale-cpu-power"]
    if node_data.has_gpu:
        steps.append("sum-gpu-power")
    steps += ["duration-to-hours", "calculate-energy", "calculate-carbon-operational"]
    return steps
```

## Plugin Files

### Delete

| Plugin | Reason |
|---|---|
| `weight-cpu-share.yaml` | Encodes the invalid 0.7 coefficient |
| `weight-mem-share.yaml` | Encodes the invalid 0.3 coefficient |
| `reservation-share.yaml` | Sums the two weighted shares; meaningless without them |
| `scale-host-power.yaml` | Scales `host_power`; host power no longer used |
| `scale-host-power-gpu.yaml` | Same, GPU variant |
| `sum-scaph-power.yaml` | Sums unscaled `cpu_power + dram_power`; replaced |
| `sum-scaph-gpu-power.yaml` | Same, GPU variant |
| `sum-node-gpu-power.yaml` | Reads `scaled_host_power_kw`; replaced by `sum-gpu-power.yaml` |

### Keep unchanged

| Plugin | Notes |
|---|---|
| `cpu-share.yaml` | Already correct — `cpu_allocated / cpu_total → cpu_share` |
| `mem-share.yaml` | Already correct — `mem_allocated / mem_total → mem_share` |
| `duration-to-hours.yaml` | Unchanged |
| `calculate-energy.yaml` | Unchanged |
| `calculate-carbon-operational.yaml` | Unchanged |
| All embodied plugins | Unchanged |

### Add

| Plugin | Output field | Notes |
|---|---|---|
| `scale-cpu-power.yaml` | `node_power_kw` | Used when `has_dram=False` |
| `scale-cpu-power-dram.yaml` | `attributed_cpu_power` | Used when `has_dram=True` |
| `scale-dram-power.yaml` | `attributed_dram_power` | Used when `has_dram=True` |
| `sum-attributed-power-dram.yaml` | `node_power_kw` | Sums cpu + dram attributed power |
| `sum-gpu-power.yaml` | `node_power_kw` | Sums `node_power_kw + gpu_power`; used when `has_gpu=True` |

## Files Changed

| File | Change |
|---|---|
| `src/jobcarbon/registry.py` | Delete `NodeProfile` enum, `GPU_PROFILES`, `HOST_PROFILES`; remove `host_power` from `METRIC_REGISTRY` |
| `src/jobcarbon/models.py` | Remove `profile: NodeProfile` from `NodeData`; add `has_dram: bool = False`, `has_gpu: bool = False` |
| `src/jobcarbon/loader.py` | Remove profile assignment; set `has_dram`/`has_gpu` from probe results; remove `host_power` query; raise `ValueError` when `cpu_results` is empty |
| `src/jobcarbon/generator.py` | Replace `OPERATIONAL_STEPS` dict with `_operational_steps(node_data)`; replace `OPERATIONAL_DEFAULTS` dict with `_operational_defaults(node_data)`; replace all `profile in GPU_PROFILES` guards with `node_data.has_gpu` |
| `src/jobcarbon/plugins/weight-cpu-share.yaml` | Delete |
| `src/jobcarbon/plugins/weight-mem-share.yaml` | Delete |
| `src/jobcarbon/plugins/reservation-share.yaml` | Delete |
| `src/jobcarbon/plugins/scale-host-power.yaml` | Delete |
| `src/jobcarbon/plugins/scale-host-power-gpu.yaml` | Delete |
| `src/jobcarbon/plugins/sum-scaph-power.yaml` | Delete |
| `src/jobcarbon/plugins/sum-scaph-gpu-power.yaml` | Delete |
| `src/jobcarbon/plugins/sum-node-gpu-power.yaml` | Delete |
| `src/jobcarbon/plugins/scale-cpu-power.yaml` | Add — `cpu_power × cpu_share → node_power_kw` |
| `src/jobcarbon/plugins/scale-cpu-power-dram.yaml` | Add — `cpu_power × cpu_share → attributed_cpu_power` |
| `src/jobcarbon/plugins/scale-dram-power.yaml` | Add — `dram_power × mem_share → attributed_dram_power` |
| `src/jobcarbon/plugins/sum-attributed-power-dram.yaml` | Add — `[attributed_cpu_power, attributed_dram_power] → node_power_kw` |
| `src/jobcarbon/plugins/sum-gpu-power.yaml` | Add — `[node_power_kw, gpu_power] → node_power_kw` |
| `METHODOLOGY.md` §2 | Replace profile table with description of metric-driven pipeline assembly; note `host_power` is not used |
| `METHODOLOGY.md` §4 | Rewrite: document per-component attribution model, scaling formulas, empirical basis |
| `tests/test_loader.py` | Remove `test_process_node_profile`; add `test_process_node_flags` parametrized over `(dram, gpu, has_dram, has_gpu)`; add test asserting `ValueError` when `cpu_results` empty |
| `tests/test_generator.py` | Update `_node()` helper to remove `profile`, add `has_dram=False`/`has_gpu=False`; rewrite host-only pipeline step tests; update defaults tests; update plugin union test |

## Validation and Acceptance Criteria

### Example arithmetic — CPU-only node

Given: `cpu_allocated=8`, `cpu_total=32`, `cpu_power=40.0` W (whole socket)

```
cpu_share              = 8 / 32                   = 0.25
attributed_cpu_power   = 40.0 × 0.25              = 10.0 W
node_power_kw          = 10.0 W
energy (60s)           = 10.0 / 1000 × 60 / 3600  = 0.000167 kWh
```

### Example arithmetic — CPU + DRAM node

Given: `cpu_allocated=8`, `cpu_total=32`, `mem_allocated=32`, `mem_total=128`,
`cpu_power=40.0` W, `dram_power=8.0` W

```
cpu_share              = 8 / 32   = 0.25
attributed_cpu_power   = 40.0 × 0.25 = 10.0 W
mem_share              = 32 / 128 = 0.25
attributed_dram_power  = 8.0 × 0.25  =  2.0 W
node_power_kw          = 10.0 + 2.0  = 12.0 W
```

### Example arithmetic — CPU + GPU node

Given: same CPU as above, `gpu_power=150.0` W (sum of job's GPUs, already job-filtered)

```
attributed_cpu_power    = 10.0 W
node_power_kw (pre-GPU) = 10.0 W
node_power_kw           = 10.0 + 150.0 = 160.0 W
```

### Acceptance criteria

- No manifest references `host_power`, `reservation_share`, `weighted_cpu_share`,
  `weighted_mem_share`, or `scaled_host_power_kw`.
- CPU-only node manifests contain `cpu-share` and `scale-cpu-power`; do not contain
  `mem-share`, `scale-dram-power`, or `sum-attributed-power-dram`.
- CPU+DRAM node manifests contain `scale-cpu-power-dram`, `mem-share`, `scale-dram-power`,
  and `sum-attributed-power-dram`; do not contain `scale-cpu-power`.
- GPU node manifests contain `sum-gpu-power` immediately before `duration-to-hours`.
- `_node_defaults` injects `cpu_total`, `cpu_allocated`, `mem_total`, `mem_allocated` for
  all nodes.
- `_process_node` raises `ValueError` when no `cpu_power` timeseries is returned.
- All existing passing tests continue to pass after updates. No new test references
  `NodeProfile`.

## Open Questions / Future Work

- **DRAM domain node enumeration:** the exact set of nodes with DRAM domain data is not
  precisely enumerated. Consider logging which combination of flags resolved for each node
  during `process_job` for operator visibility.
- **`host_power` cross-check:** removing `host_power` from `METRIC_REGISTRY` means it will
  never appear in manifests. If future work wants to validate attribution accuracy (attributed
  sum vs measured host total), it would need to be re-added as an informational field only.

## References

- METHODOLOGY.md §2, §4 — current (incorrect) profile table and host-power scaling description
- `src/jobcarbon/loader.py` — `_process_node`, profile assignment logic
- `src/jobcarbon/generator.py` — `OPERATIONAL_STEPS`, `OPERATIONAL_DEFAULTS`
- `src/jobcarbon/plugins/sum-scaph-power.yaml` — current unscaled sum (to be deleted)
- Scaphandre documentation — `scaph_socket_power_microwatts`, `scaph_domain_power_microwatts`
