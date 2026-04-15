# Methodology

This document describes the carbon estimation methodology used by `jobcarbon`.

## 1. Purpose and scope

`jobcarbon` implements the [Software Carbon Intensity (SCI) specification][sci-spec] defined by the Green Software Foundation. SCI is a *comparison* metric, not an absolute carbon accounting tool. The value is in comparing jobs against each other (e.g. algorithm variants, different resource requests, different scheduling times) on a common, reproducible scale. It does not claim to represent the precise real-world carbon footprint of the job

**Scope:** operational energy (direct compute power draw) and embodied carbon of compute hardware (manufacture and end-of-life, amortised over hardware lifetime).

The following are **explicitly out of scope**:
- Network I/O energy and embodied carbon
- Storage energy and embodied carbon

For HPC batch jobs, network and storage are minor contributors relative to compute [Lottick et al. 2019; Patterson et al. 2021], and per-job attribution is not readily available from the Prometheus telemetry in scope.

## 2. Power telemetry and node profiles

Power measurements are drawn from Prometheus at the 60-second scrape resolution used by Oscar's monitoring stack. The tool selects a measurement profile for each node at job time by probing Prometheus for the presence of component-level metrics:

| Profile | Condition | Power source(s) |
|---|---|---|
| `full` | CPU and DRAM component power present | Scaphandre CPU + DRAM |
| `full_gpu` | CPU, DRAM, and GPU power present | Scaphandre CPU + DRAM + NVIDIA GPU |
| `host_only` | Only whole-host power present | Scaphandre host power, scaled by reservation share |
| `host_only_gpu` | Whole-host and GPU power present | Scaphandre host power (scaled) + NVIDIA GPU |

**CPU and DRAM power** are read from Scaphandre's `scaph_socket_power_microwatts` and `scaph_domain_power_microwatts{domain_name="dram"}` metrics respectively. Both are reported in microwatts and converted to watts within the Impact Framework pipeline.

**Whole-host power** is read from Scaphandre's `scaph_host_power_microwatts`, also in microwatts, converted the same way.

**GPU power** is read from `nvidia_gpu_power_usage_milliwatts`, filtered to the job's cgroup via the `jobid` label and summed across all GPUs assigned to the job. The PromQL query converts milliwatts to microwatts, making GPU power unit-consistent with all Scaphandre metrics before the in-pipeline conversion to watts.

## 3. Operational carbon

### Per-interval energy

For each 60-second observation interval, per-node energy is computed as:

```
power (kWh) = node_power_watts / 1000 * duration_s / 3600
```

The output field is named `power` in the manifest with unit kWh per scrape interval.

**Aggregation:** `power` is declared with `aggregation-method: {time: avg, component: sum}` in the Impact Framework manifest. `power` is an energy-per-interval value (a rate) metric; summing over timesteps would overcount when `if-run` aggregates across the job duration. Components (nodes) are summed.

### Grid carbon intensity

Operational carbon per interval is:

```
carbon_operational (gCO2eq) = grid_carbon_intensity (gCO2eq/kWh) × power (kWh)
```

The grid carbon intensity is hardcoded at **381 gCO2eq/kWh**. This value is derived from the EPA eGRID 2022 dataset, subregion NEWE (New England), which reports an annual average CO2-equivalent emission rate of 840 lb CO2eq/MWh. The Oscar cluster is located in Providence, Rhode Island, served by ISO-NE within the NEWE subregion.

This is a static annual average. It does not reflect the temporal variation in grid carbon intensity across hours, days, or seasons. The plan to replace this with temporally-resolved marginal intensity (MOER) is described in `FUTURE.md`.

## 4. `host_only` reservation-share attribution

When only whole-host power is available, the fraction of host power attributed to a job is computed as:

```
reservation_share = 0.7 * (cpu_allocated / cpu_total)
                  + 0.3 * (mem_allocated / mem_total)
```

```
node_power_watts = host_power_watts × reservation_share
```

where `cpu_allocated` and `cpu_total` are core counts, and `mem_allocated` and `mem_total` are both in bytes (so the ratio is dimensionless).

**The 0.7/0.3 split is a placeholder.** It encodes a general prior that CPU activity is a larger driver of host power draw than memory activity. It has not been validated against measured data on Oscar's specific hardware. Any results derived from the `host_only` or `host_only_gpu` profiles should be interpreted with this limitation in mind.

The correct approach — offline empirical characterisation using nodes that have both `host_power` and component-level Scaphandre data — is planned. See `FUTURE.md §2`.

## 5. Embodied carbon

Embodied carbon is computed using the Impact Framework `SciEmbodied` plugin, which implements the [SCI-M equation][sci-m] from the SCI specification.

**Inputs passed to the plugin:**

| Input | Value | Source |
|---|---|---|
| `vCPUs` | `cpu_allocated` | Cores allocated to the job (from cgroup data) |
| `memory` | `memory_gb` | Memory allocated to the job in GB (from cgroup data) |
| `lifespan` | 157,680,000 s | 5 × 365 days; Oscar's hardware refresh cycle |

**Attribution model:** `SciEmbodied` returns the embodied carbon of the whole server scaled to the job's allocated share of CPU and memory resources. This follows the SCI specification's resource-share attribution approach.

**Limitations:**

- The Boavista dataset underlying `SciEmbodied` consists primarily of commercial server and cloud instance profiles. Interpolation accuracy for research HPC hardware is uncertain.
- **GPU embodied carbon is not included.** For `full_gpu` and `host_only_gpu` jobs this is a known underestimate. GPU manufacturing is energy- and materials-intensive; the omission is non-trivial for GPU-heavy workloads. The planned approach is described in `FUTURE.md §3`. When a GPU model is not found in the lookup table, the fallback is the average embodied carbon across all entries in the table rather than zero; this is flagged in output.

## 6. SCI score

The final score is:

```
carbon (gCO2eq) = carbon_operational + carbon_embodied
```

per job run (`R = 1`). This is the value reported in `tree.children.<node>.aggregated` in the `if-run` output, summed across all nodes in the job.

No normalisation denominator is applied beyond `R = 1`. For cross-job comparison on a per-resource-unit basis (e.g. per GPU-hour, per unit of scientific output), users should apply normalisation externally.

[sci-spec]: https://sci-guide.greensoftware.foundation/
[sci-m]: https://sci-guide.greensoftware.foundation/M
