# Methodology

This document describes the carbon estimation methodology used by `jobcarbon`

## 1. Purpose and Scope

`jobcarbon` implements the [Software Carbon Intensity (SCI) specification][sci-spec] defined by the Green Software Foundation. SCI is a *comparison* metric, not an absolute carbon accounting tool. The value is in comparing jobs against each other (e.g. algorithm variants, different resource requests, different scheduling times) on a common, reproducible scale. It does not claim to represent the precise real-world carbon footprint of the job

**Scope:** operational energy (direct compute power draw) and embodied carbon of compute hardware (manufacture and end-of-life, amortized over hardware lifetime)

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

**Aggregation:** `power` is declared with `aggregation-method: {time: avg, component: sum}` in the Impact Framework manifest `power` is an energy-per-interval value (a rate) metric; summing over timesteps would overcount when `if-run` aggregates across the job duration Components (nodes) are summed

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
node_power_watts = host_power_watts × reservation_share
```

where `cpu_allocated` and `cpu_total` are core counts, and `mem_allocated` and `mem_total` are both in bytes (so the ratio is dimensionless)

**The 0.7/0.3 split is a placeholder**. It encodes a general prior that CPU activity is a larger driver of host power draw than memory activity. It has not been validated against measured data on Oscar's specific hardware. Any results derived from the `host_only` or `host_only_gpu` profiles should be interpreted with this limitation in mind

The correct approach — offline empirical characterization using nodes that have both `host_power` and component-level Scaphandre data — is planned See `FUTURE.md §2`

## 5. Embodied Carbon

### CPU and Server Embodied Carbon

Embodied carbon is computed using the Impact Framework `SciEmbodied` plugin, which implements the [SCI-M equation][sci-m] from the SCI specification

**Inputs passed to the plugin:**

| Input | Value | Source |
|---|---|---|
| `vCPUs` | `cpu_allocated` | Cores allocated to the job (from cgroup data) |
| `memory` | `memory_gb` | Memory allocated to the job in GB (from cgroup data) |
| `lifespan` | 157,680,000 seconds | 5 years; Oscar's hardware refresh cycle |

**Attribution model:** `SciEmbodied` returns the embodied carbon of the whole server scaled to the job's allocated share of CPU and memory resources This follows the SCI specification's resource-share attribution approach

**Limitations:**

- The Boavista dataset underlying `SciEmbodied` consists primarily of commercial server and cloud instance profiles. Interpolation accuracy for research HPC hardware is uncertain

### GPU Embodied Carbon

GPU embodied carbon is added to `carbon_embodied` separately from the CPU/server component, using one of two paths depending on whether an authoritative cradle-to-gate figure is available for the GPU model.

All figures cover **manufacturing only (cradle-to-gate)**. Use-phase emissions are accounted for separately via `carbon_operational` (§3); using a full lifecycle figure here would double-count operational emissions.

The operative values are stored in `config/gpu_embodied.toml` and are subject to revision as better sources become available. The per-GPU values and sources documented here reflect the current state of that file.

**GPU count** (`gpu_count`) is the number of GPUs assigned to the job on a given node, obtained at job load time by counting distinct `minor_number` label values in `nvidia_gpu_power_usage_milliwatts` for the job and node.

#### Tier 1 — Manufacturer PCF / Third-Party LCA

Where a cradle-to-gate figure from a manufacturer product carbon footprint (PCF) document or peer-reviewed lifecycle assessment (LCA) is available, it is used directly. The manifest pipeline multiplies the per-GPU figure by `gpu_count` to obtain the node-level embodied carbon.

| GPU model | gCO2eq per GPU | Derivation | Source |
|---|---|---|---|
| `a100` | 127,600 | Per single GPU, cradle-to-gate | Lannelongue et al., "More than Carbon: Cradle-to-Grave Environmental Impacts of GenAI Training on the Nvidia A100 GPU," arXiv 2025 ([arXiv:2509.00093][a100-lca]) |
| `h100` | 164,000 | 1,312 kgCO2eq system ÷ 8 GPUs; materials and components = 91% of full lifecycle | [NVIDIA HGX H100 Product Carbon Footprint][h100-pcf] |
| `nvidia_b200` | 284,250 | 2,274 kgCO2eq system ÷ 8 GPUs; materials and components = 94% of full lifecycle | [NVIDIA HGX B200 Product Carbon Footprint][b200-pcf] |

#### Tier 2 — Per-Node Scalar Lookup

For GPU models without an authoritative cradle-to-gate figure, embodied carbon is estimated from GPU die area and VRAM capacity. The arithmetic is executed inside the Impact Framework pipeline so all intermediate values are visible in the manifest:

```
die_area_cm2_yield_corrected  = die_area_cm2 × (1 / yield)
chip_embodied_kgco2eq         = die_area_cm2_yield_corrected × process_scalar_kgco2eq_per_cm2
vram_embodied_kgco2eq         = vram_gb × mem_scalar_kgco2eq_per_gb
gpu_embodied_kgco2eq_per_gpu  = chip_embodied_kgco2eq + vram_embodied_kgco2eq
gpu_embodied_kgco2eq_node     = gpu_embodied_kgco2eq_per_gpu × gpu_count
gpu_embodied_gco2eq           = gpu_embodied_kgco2eq_node × 1000
```

**Yield correction**

The paper figures below are per cm² of wafer area processed. A yield correction divides by the fraction of dies that are functional, allocating the full wafer carbon to good dies only. A **conservative yield of 90% (0.9)** is assumed. Boakes et al. ([IEEE IEDM 2023][boakes2023]) report that large GPU dies (e.g. GA102 at 628 mm²) have a peak yield of approximately 55%; smaller or older dies yield considerably higher. Using 90% understates the embodied carbon per good die for large dies — estimates derived from it should be read as lower bounds.

**Manufacturing process scalar** (resolved from `process_nm` in `config/gpu_embodied.toml`):

Scalars are derived from per-process-node global warming potential (GWP) figures in Boakes et al., "Cradle-to-gate life cycle assessment of CMOS logic technologies," [IEEE IEDM 2023][boakes2023] (Table II). The paper covers TSMC nodes N28 through A14. Yield correction at 90% is applied on top of the wafer-level figures.

When multiple EUV/non-EUV variants exist for the same nominal node, the lower (less carbon-intensive) figure is used as a conservative estimate.

| `process_nm` | TSMC node | Wafer kgCO2eq/cm² (Boakes) | Yield-corrected scalar (÷ 0.9) |
|---|---|---|---|
| 28 | N28 | 1.38 | 1.53 |
| 20 | N20 | 1.47 | 1.63 |
| 14 | N14 | 1.55 | 1.72 |
| 10 | N10 | 1.78 | 1.98 |
| 7 | N7 (non-EUV) / N7EUV lower | 2.06 | 2.29 |
| 5 | N5 | 2.42 | 2.69 |
| 4 | N3 (closest documented) | 2.74 | 3.04 |
| 3 | N3 | 2.74 | 3.04 |
| 2 | N2 | 2.73 | 3.03 |

**Samsung 8N exception:** GA102-based GPUs on Oscar (RTX 3090, A5000, A5500, A40, A6000 (Ampere), A2) are manufactured on Samsung 8N. Samsung 8N is not covered by Boakes et al., which is TSMC-specific. TSMC N7 (yield-corrected scalar 2.29 kgCO2eq/cm²) is used as a conservative proxy — Samsung 8N is a derivative of N10/N7-class lithography and N7 is the closest documented analogue in the conservative direction. These entries are flagged `process_nm: 8` in `config/gpu_embodied.toml`; `gpu_config.py` maps `8` → the N7 scalar with a logged warning.

**Memory type scalar** (resolved from `mem_type` in `config/gpu_embodied.toml`):

| Memory type | `mem_scalar_kgco2eq_per_gb` |
|---|---|
| `gddr6` | 0.4 |
| `hbm2` | 0.9 |
| `hbm2e` | 0.9 |
| `hbm3` | 0.9 |

HBM2 and HBM2e are treated as equal. Memory scalars from: Li, Graif, and Gupta, "Towards Carbon-efficient LLM Life Cycle," [HotCarbon 2024][hotcarbon2024] (Table 1). Values are taken from the paper as published; independent verification against primary manufacturer data has not been performed.

**GPU models currently on the regression path** (no authoritative cradle-to-gate figure available):

| GPU model (sinfo) | Die | Foundry / node | `die_area_cm2` | `vram_gb` | `process_nm` | `mem_type` | Process scalar note |
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

If a GPU-profile node has no entry in `config/gpu_embodied.toml`, manifest generation fails with a clear error. There is no silent fleet-average fallback.

## 6. SCI Score

The final score is:

```
carbon (gCO2eq) = carbon_operational + carbon_embodied
```

per job run (`R = 1`) This is the value reported in `tree.children.<node>.aggregated` in the `if-run` output, summed across all nodes in the job

No normalization denominator is applied beyond `R = 1` For cross-job comparison on a per-resource-unit basis (e g per GPU-hour, per unit of scientific output), users should apply normalization externally

[sci-spec]: https://sci-guide.greensoftware.foundation/
[sci-m]: https://sci-guide.greensoftware.foundation/M
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
