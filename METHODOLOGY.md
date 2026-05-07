# Methodology

This document describes the carbon estimation methodology used by `jobcarbon`

## 1. Purpose and Scope

`jobcarbon` produces per-job carbon estimates in the [Impact Framework (IMP)][if-spec] manifest format defined by the Green Software Foundation. The value is in comparing jobs against each other (e.g. algorithm variants, different resource requests, different scheduling times) on a common, reproducible scale. It does not claim to represent the precise real-world carbon footprint of the job

**Scope:** operational energy (direct compute power draw) and, optionally with `--embodied`, embodied carbon of compute hardware (manufacture and end-of-life, amortized over hardware lifetime)

The following are **explicitly out of scope**:
- Network I/O operational and embodied carbon
- Storage operational and embodied carbon

Per-job attribution of network and power/storage carbon is not readily available from the Prometheus telemetry in scope. [Li et al., HotCarbon 2024][hotcarbon2024] models network and disk as constants. This tool does not add any constants for network or disk

## 2. Power Telemetry and Node Profiles

Power measurements are drawn from Prometheus at the 60-second scrape resolution used by Oscar's monitoring stack. The tool selects a measurement profile for each node at job time by probing Prometheus for the presence of component-level metrics:

| Profile | Condition | Power source(s) |
|---|---|---|
| `full` | CPU and DRAM component power present | Scaphandre CPU + DRAM |
| `full_gpu` | CPU, DRAM, and GPU power present | Scaphandre CPU + DRAM + Nvidia GPU |
| `host_only` | Only whole-host power present | Scaphandre host power, scaled by reservation share |
| `host_only_gpu` | Whole-host and GPU power present | Scaphandre host power (scaled) + Nvidia GPU |

**CPU and DRAM power** are read from Scaphandre's `scaph_socket_power_microwatts` and `scaph_domain_power_microwatts{domain_name="dram"}` metrics respectively Both are reported in microwatts and converted to watts within the Impact Framework pipeline

**Whole-host power** is read from Scaphandre's `scaph_host_power_microwatts`, also in microwatts, converted the same way

**GPU power** is read from `nvidia_gpu_power_usage_milliwatts`, filtered to the job's cgroup via the `jobid` label and summed across all GPUs assigned to the job. The PromQL query converts milliwatts to microwatts, making GPU power unit-consistent with all Scaphandre metrics before the in-pipeline conversion to watts

## 3. Operational Carbon

### Per-Interval Energy

For each 60-second observation interval, per-node energy is computed as:

```
power (kWh) = node_power_watts / 1000 * duration_s / 3600
```

The output field is named `power` in the manifest with unit kWh per scrape interval

**Aggregation:** `power` is declared with `aggregation-method: {time: sum, component: sum}` in the Impact Framework manifest. Summing across timesteps gives the total energy consumed by the job on that node. Summing across components (nodes) gives the job-wide total. `carbon_operational` is derived per-timestep before aggregation, so there is no double-counting.

### Grid Carbon Intensity

Operational carbon per interval is:

```
carbon_operational (gCO2eq) = grid_carbon_intensity (gCO2eq/kWh) × power (kWh)
```

The grid carbon intensity is hardcoded at **381 gCO2eq/kWh**. This value is derived from the [EPA eGRID 2022][egrid2022] dataset, which reports an annual average CO2-equivalent emission rate of **840 lb CO2eq/MWh** for Rhode Island 

This is a static annual average. It does not reflect the temporal variation in grid carbon intensity across hours, days, or seasons The plan to replace this with temporally-resolved marginal intensity (MOER) is described in `FUTURE.md`

## 4. `host_only` Reservation-Share Attribution

When only whole-host power is available, the fraction of host power attributed to a job is computed as:

```
reservation_share = 0.7 * (cpu_allocated / cpu_total)
                  + 0.3 * (mem_allocated / mem_total)
```

```
node_power_kw = host_power × reservation_share
```

where `cpu_allocated` and `cpu_total` are core counts, and `mem_allocated` and `mem_total` are both in GiB (so the ratio is dimensionless)

**The 0.7/0.3 split is a placeholder**. It encodes a general prior that CPU activity is a larger driver of host power draw than memory activity. It has not been validated against measured data on Oscar's specific hardware. Any results derived from the `host_only` or `host_only_gpu` profiles should be interpreted with this limitation in mind

The correct approach — offline empirical characterization using nodes that have both `host_power` and component-level Scaphandre data — is planned See `FUTURE.md §2`

## 5. Embodied Carbon (`--embodied`)

Embodied carbon estimation is opt-in via the `--embodied` flag. When not specified, `carbon_operational` is the terminal output and no embodied steps are run.

### Server Embodied Carbon

Embodied carbon for the server platform is computed using the Impact Framework `SciEmbodied` plugin. The output field is `server_embodied_carbon`, which is then summed into `carbon_embodied`.

**Inputs passed to the plugin:**

| Plugin input | Manifest field | Description |
|---|---|---|
| `vCPUs` | `cpu_allocated` | Cores allocated to the job (from cgroup data) |
| `memory` | `mem_allocated` | Memory allocated to the job in GiB (from cgroup data) |
| `lifespan` | `cpu_lifespan_seconds` | Server amortisation period; default 5 years, configurable via `cpu_lifespan_years` in `jobcarbon.toml` or `JOBCARBON_CPU_LIFESPAN_YEARS` |

**What `SciEmbodied` models:** the plugin estimates embodied carbon for the *entire server* — CPU, DRAM, chassis, PSU, and associated components — not the processor die alone. It then scales the result to the job's allocated share of vCPUs and memory.

**Per-timestep amortization:** `SciEmbodied` internally scales the server lifetime total by `duration / lifespan`, producing a per-observation-interval value. Summed over the full job, this equals the job's proportional share of the server's lifetime embodied carbon

**Limitations:**

- The Boavista dataset underlying `SciEmbodied` consists primarily of commercial server and cloud instance profiles. Interpolation accuracy for research HPC hardware is uncertain

### GPU Embodied Carbon

GPU embodied carbon is computed separately from the server component and added to `carbon_embodied` via the `sum-embodied-gpu` pipeline step. All figures cover **manufacturing only (cradle-to-gate)**. Use-phase emissions are accounted for separately via `carbon_operational` (§3); using a full lifecycle figure here would double-count operational emissions.

**Per-timestep amortization:** GPU embodied carbon is time-scaled using the same `duration / lifespan` approach as the server. The pipeline divides the node-level lifetime total by `gpu_lifespan_seconds` to obtain a per-second rate, then multiplies by `duration` to obtain the per-interval value. The GPU lifespan defaults to 5 years and is configurable via `gpu_lifespan_years` in `jobcarbon.toml` or `JOBCARBON_GPU_LIFESPAN_YEARS`.

**GPU count** (`gpu_count`) is the number of GPUs assigned to the job on a given node, obtained at job load time by counting distinct `minor_number` label values in `nvidia_gpu_power_usage_milliwatts` for the job and node.

The operative values are stored in `config/jobcarbon.toml` and are subject to revision as better sources become available. The per-GPU values and sources documented here reflect the current state of that file.

#### Tier 1 — Manufacturer PCF / Third-Party LCA

Where a cradle-to-gate figure from a manufacturer product carbon footprint (PCF) document or peer-reviewed lifecycle assessment (LCA) is available, it is used directly. The pipeline scales to this timestep as follows:

```
gpu_embodied_carbon_node      = pcf_carbon_per_gpu × gpu_count          # lifetime total, all GPUs
gpu_embodied_carbon_per_second = gpu_embodied_carbon_node / gpu_lifespan_seconds
gpu_embodied_carbon           = gpu_embodied_carbon_per_second × duration  # this timestep
```

| GPU model | gCO2eq per GPU | Derivation | Source |
|---|---|---|---|
| `a100` | 127,600 | Per single GPU, cradle-to-gate | Lannelongue et al., "More than Carbon: Cradle-to-Grave Environmental Impacts of GenAI Training on the Nvidia A100 GPU," arXiv 2025 ([arXiv:2509.00093][a100-lca]) |
| `h100` | 164,000 | 1,312 kgCO2eq system ÷ 8 GPUs; materials and components = 91% of full lifecycle | [NVIDIA HGX H100 Product Carbon Footprint][h100-pcf] |
| `nvidia_b200` | 284,250 | 2,274 kgCO2eq system ÷ 8 GPUs; materials and components = 94% of full lifecycle | [NVIDIA HGX B200 Product Carbon Footprint][b200-pcf] |

#### Tier 2 — Per-Node Scalar Lookup

For GPU models without an authoritative cradle-to-gate figure, embodied carbon is estimated from GPU die area and VRAM capacity. The arithmetic is executed inside the Impact Framework pipeline so all intermediate values are visible in the manifest:

```
chip_embodied_carbon          = die_area_sq_cm × process_scalar_carbon_per_sq_cm   # per GPU, lifetime
vram_embodied_carbon          = vram_gb × mem_scalar_carbon_per_gb                  # per GPU, lifetime
gpu_embodied_carbon_per_gpu   = chip_embodied_carbon + vram_embodied_carbon
gpu_embodied_carbon_node      = gpu_embodied_carbon_per_gpu × gpu_count             # all GPUs, lifetime
gpu_embodied_carbon_per_second = gpu_embodied_carbon_node / gpu_lifespan_seconds
gpu_embodied_carbon           = gpu_embodied_carbon_per_second × duration           # this timestep
```

All intermediate carbon fields are in gCO2eq. `process_scalar_carbon_per_sq_cm` and `mem_scalar_carbon_per_gb` are injected into each node's `defaults` block by the generator, resolved from the GPU's `process` and `mem_type` fields in `config/jobcarbon.toml`

**Yield correction**

The paper figures below are per cm² of wafer area processed. A yield correction divides by the fraction of dies that are functional, allocating the full wafer carbon to good dies only. A **conservative yield of 90% (0.9)** is assumed. Boakes et al. ([IEEE IEDM 2023][boakes2023]) report that large GPU dies (e.g. GA102 at 628 mm²) have a peak yield of approximately 55%; smaller or older dies yield considerably higher. Using 90% understates the embodied carbon per good die for large dies — estimates derived from it should be read as lower bounds.

Yield correction is applied when computing `process_scalar_carbon_per_sq_cm` in `config.py` (i.e., `wafer_scalar / 0.9`). It is **not** a pipeline step in the manifest; the value injected into each node's `defaults` block is already yield-corrected.

**Manufacturing process scalar** (resolved from `process` in `config/jobcarbon.toml`):

Scalars are derived from per-process-node global warming potential (GWP) figures in Boakes et al., "Cradle-to-gate life cycle assessment of CMOS logic technologies," [IEEE IEDM 2023][boakes2023] (Table II). The paper covers TSMC nodes N28 through A14. Yield correction at 90% is applied on top of the wafer-level figures.

When multiple EUV/non-EUV variants exist for the same nominal node, the lower (less carbon-intensive) figure is used as a conservative estimate.

| `process_nm` | TSMC node | Wafer kgCO2eq/cm² (Boakes) | Yield-corrected `process_scalar_carbon_per_sq_cm` (gCO2eq/cm²) |
|---|---|---|---|
| 28 | N28 | 1.38 | 1533 |
| 20 | N20 | 1.47 | 1633 |
| 14 | N14 | 1.55 | 1722 |
| 10 | N10 | 1.78 | 1978 |
| 7 | N7 (non-EUV) / N7EUV lower | 2.06 | 2289 |
| 5 | N5 | 2.42 | 2689 |
| 4 | N3 (closest documented) | 2.74 | 3044 |
| 3 | N3 | 2.74 | 3044 |
| 2 | N2 | 2.73 | 3033 |

**Samsung 8N exception:** GA102-based GPUs on Oscar (RTX 3090, A5000, A5500, A40, A6000 (Ampere), A2) are manufactured on Samsung 8N. Samsung 8N is not covered by Boakes et al., which is TSMC-specific. TSMC N7 (yield-corrected scalar 2289 gCO2eq/cm²) is used as a conservative proxy — Samsung 8N is a derivative of N10/N7-class lithography and N7 is the closest documented analogue in the conservative direction. These entries are flagged `process: samsung-8n` in `config/jobcarbon.toml`; `config.py` maps `samsung-8n` → the N7 scalar with a logged warning.

**Memory type scalar** (resolved from `mem_type` in `config/jobcarbon.toml`):

| Memory type | `mem_scalar_carbon_per_gb` (gCO2eq/GiB) |
|---|---|
| `gddr6` | 400 |
| `hbm2` | 900 |
| `hbm2e` | 900 |
| `hbm3` | 900 |

HBM2 and HBM2e are treated as equal. Memory scalars from: Li, Graif, and Gupta, "Towards Carbon-efficient LLM Life Cycle," [HotCarbon 2024][hotcarbon2024] (Table 1). Values are taken from the paper as published; independent verification against primary manufacturer data has not been performed.

**GPU models currently on the regression path** (no authoritative cradle-to-gate figure available):

| GPU model (sinfo) | Die | Foundry / node | `die_area_sq_cm` | `vram_gb` | `process` | `mem_type` | Process scalar note |
|---|---|---|---|---|---|---|---|
| `quadro_rtx_6000` | TU102 | TSMC 12N | 7.54 ([TechPowerUp][tpu-tu102]) | 24.0 | 12 | gddr6 | N14 proxy (closest) |
| `nvidia_geforce_rtx_3090` | GA102 | Samsung 8N | 6.28 ([TechPowerUp][tpu-ga102]) | 24.0 | 8 | gddr6 | N7 proxy — Samsung 8N not in Boakes |
| `a5500` | GA102 | Samsung 8N | 6.28 ([TechPowerUp][tpu-ga102]) | 24.0 | 8 | gddr6 | N7 proxy |
| `nvidia_rtx_a5000` | GA102 | Samsung 8N | 6.28 ([TechPowerUp][tpu-ga102]) | 24.0 | 8 | gddr6 | N7 proxy |
| `nvidia_a40` | GA102 | Samsung 8N | 6.28 ([TechPowerUp][tpu-ga102]) | 48.0 | 8 | gddr6 | N7 proxy |
| `nvidia_rtx_a6000` | GA102 | Samsung 8N | 6.28 ([TechPowerUp][tpu-ga102]) | 48.0 | 8 | gddr6 | N7 proxy |
| `a6000` | GA102 | Samsung 8N | 6.28 ([TechPowerUp][tpu-ga102]) | 48.0 | 8 | gddr6 | N7 proxy |
| `a2` | GA107 | Samsung 8N | 2.00 ([TechPowerUp][tpu-ga107]) | 16.0 | 8 | gddr6 | N7 proxy |
| `l40` | AD102 | TSMC N4 | 6.09 ([TechPowerUp][tpu-ad102]) | 48.0 | 4 | gddr6 | N3 proxy (closest) |
| `l40s` | AD102 | TSMC N4 | 6.09 ([TechPowerUp][tpu-ad102]) | 48.0 | 4 | gddr6 | N3 proxy |
| `nvidia_h100_nvl` | GH100 | TSMC N4 | 8.14 ([Chips and Cheese][cnc-h100]) | 94.0 | 4 | hbm2e | N3 proxy |
| `nvidia_gh200_480gb` | GH100 (GPU die only) | TSMC N4 | 8.14 ([Chips and Cheese][cnc-h100]) | 480.0 | 4 | hbm3 | N3 proxy |
| `nvidia_rtx_pro_6000_blackwell_max-q` | GB202 | TSMC N4P | 7.50 ([Chips and Cheese][cnc-blackwell]) | 96.0 | 4 | gddr6 | N3 proxy |

Die area figures are sourced from [TechPowerUp GPU Database][techpowerup] (TU102, GA102, GA107, AD102) and die-level analyses by Chips and Cheese ([GH100][cnc-h100], [GB202][cnc-blackwell]). The GB202 figure (750 mm²) is from the Chips and Cheese Blackwell analysis; it should be updated if NVIDIA or a third-party LCA publishes a revised figure.

`nvidia_h100_nvl` uses the regression path because no manufacturer PCF is available for the NVL variant. It shares the GH100 die with the H100 SXM5 but carries more VRAM (94 GB vs 80 GB). TDP-based scaling from the H100 PCF is not used because TDP reflects clocking and power limits rather than die area or DRAM capacity, and would scale in the wrong direction relative to the additional memory

TSMC 12N (used by TU102) is marketed as a 12nm node but is architecturally closer to N14; the N14 scalar is used.

#### Failure Policy

If a GPU-profile node has no entry in `config/jobcarbon.toml`, manifest generation fails with a clear error. There is no silent fleet-average fallback.

## 6. Output

The terminal output field depends on whether `--embodied` is used:

**Operational only (default):**
```
carbon_operational (gCO2eq)   — per node, per timestep; aggregates to job total
```

**With `--embodied`:**
```
carbon_embodied (gCO2eq) = server_embodied_carbon + gpu_embodied_carbon   # GPU profiles
carbon_embodied (gCO2eq) = server_embodied_carbon                         # non-GPU profiles
carbon          (gCO2eq) = carbon_operational + carbon_embodied
```

Both are reported in `tree.children.<node>.aggregated` in the `if-run` output, summed across all nodes in the job

No normalization denominator is applied. For cross-job comparison on a per-resource-unit basis (e.g. per GPU-hour, per unit of scientific output), users should apply normalization externally

[if-spec]: https://if.greensoftware.foundation/
[egrid2022]: https://www.epa.gov/egrid/detailed-data
[a100-lca]: https://arxiv.org/abs/2509.00093
[h100-pcf]: https://images.nvidia.com/aem-dam/Solutions/documents/HGX-H100-PCF-Summary.pdf
[b200-pcf]: https://images.nvidia.com/aem-dam/Solutions/documents/HGX-B200-PCF-Summary.pdf
[boakes2023]: https://ieeexplore.ieee.org/document/10413725
[hotcarbon2024]: https://hotcarbon.org/assets/2024/pdf/hotcarbon24-final154.pdf
[techpowerup]: https://www.techpowerup.com/gpu-specs/
[tpu-tu102]: https://www.techpowerup.com/gpu-specs/nvidia-tu102.g813
[tpu-ga102]: https://www.techpowerup.com/gpu-specs/nvidia-ga102.g930
[tpu-ga107]: https://www.techpowerup.com/gpu-specs/nvidia-ga107.g988
[tpu-ad102]: https://www.techpowerup.com/gpu-specs/nvidia-ad102.g1005
[cnc-h100]: https://chipsandcheese.com/p/nvidias-h100-funny-l2-and-tons-of-bandwidth
[cnc-blackwell]: https://chipsandcheese.com/p/blackwell-nvidias-massive-gpu
