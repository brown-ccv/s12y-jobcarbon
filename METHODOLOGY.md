# Methodology

This document describes the carbon estimation methodology used by `jobcarbon`

## 1. Purpose and Scope

`jobcarbon` produces per-job carbon estimates in the [Impact Framework (IMP)][if-spec] manifest format defined by the Green Software Foundation. The value is in comparing jobs against each other (e.g. algorithm variants, different resource requests, different scheduling times) on a common, reproducible scale. It does not claim to represent the precise real-world carbon footprint of the job

**Scope:** operational energy (direct compute power draw) and, optionally with `--embodied`, embodied carbon of compute hardware (manufacture and end-of-life, amortized over hardware lifetime)

The following are **explicitly out of scope**:
- Network I/O operational and embodied carbon
- Storage operational and embodied carbon

Per-job attribution of network and power/storage carbon is not readily available from the Prometheus telemetry in scope. [Li et al., HotCarbon 2024][hotcarbon2024] models network and disk as constants. This tool does not add any constants for network or disk

## 2. Power Telemetry and Pipeline Assembly

Power measurements are drawn from Prometheus at the 60-second scrape resolution used by Oscar's monitoring stack. For each node, the tool probes which power domain metrics are available and assembles a pipeline from what is found. There is no fixed profile enum — the pipeline is built dynamically per node.

**CPU power** is read from Scaphandre's `scaph_socket_power_microwatts`. This metric is available on all Oscar nodes and its absence is treated as a hard error. It reports whole-socket power and must be scaled by the job's CPU reservation share before use.

**DRAM power** is read from Scaphandre's `scaph_domain_power_microwatts{domain_name="dram"}`. This metric is present on a subset of nodes. When present it is scaled by the job's memory reservation share. When absent the DRAM attribution step is omitted entirely.

**GPU power** is read from `nvidia_gpu_power_usage_milliwatts`, filtered to the job's assigned GPUs via the `jobid` label and summed across all GPUs on that node. Because the PromQL query filters by `jobid`, GPU power is already fully attributed to the job and requires no further scaling. GPU power is present only on GPU nodes.

`scaph_host_power_microwatts` (whole-node power) is **not used**. It carries no per-job attribution information that cannot be derived more accurately from the per-domain metrics above.

All power metrics are reported in microwatts (or milliwatts for GPU) and converted to kilowatts within the Impact Framework pipeline.

## 3. Operational Carbon

### Per-Interval Energy

For each 60-second observation interval, per-node energy is computed as:

```
energy (kWh) = node_power_watts / 1000 * duration_s / 3600
```

The output field is named `energy` in the manifest with unit kWh per scrape interval

**Aggregation:** `energy` is declared with `aggregation-method: {time: sum, component: sum}` in the Impact Framework manifest. Summing across timesteps gives the total energy consumed by the job on that node. Summing across components (nodes) gives the job-wide total. `carbon_operational` is derived per-timestep before aggregation, so there is no double-counting.

### Power Sampling and Gap Handling

Power metrics are fetched via Prometheus `query_range` at `step_seconds` intervals (default 60 s, configurable via `JOBCARBON_STEP_SECONDS`). Each returned value is the `avg_over_time` of all raw scrapes that fell within the preceding `step_seconds` window. When `step_seconds` equals the Prometheus scrape interval this is equivalent to a single instantaneous reading; when `step_seconds` is larger (as is typical for long jobs where manifest size is a concern) it averages all intermediate scrapes, reducing the influence of any single anomalous sample.

Observations are inner-joined across all power metrics for a given node (`alignment.py`). A timestamp is dropped if any metric is absent at that step. After the join, each observation is assigned a `duration` equal to the gap to the next timestamp, with the final observation receiving `step_seconds` as its duration. This means:

- **Sparse windows:** if raw scrapes are missing within a `step_seconds` window, `avg_over_time` averages fewer samples but still returns a point — the join is unaffected.
- **Complete blackouts:** if an entire step window has no raw scrapes for any metric, that timestamp is absent from `query_range` output and is therefore absent from the join. The preceding observation's `duration` is extended to cover the gap, and its power value — an average of scrapes from the window *before* the gap — is used to estimate energy during the gap interval. This is an approximation; the true power during the gap is unknown.

In practice, approximately 13% of observation windows have a duration larger than `step_seconds`, indicating gaps. Energy estimates for those intervals should be interpreted as extrapolations from the nearest available measurement.

### Grid Carbon Intensity

Operational carbon per interval is:

```
carbon_operational (gCO2eq) = grid_carbon_intensity (gCO2eq/kWh) × energy (kWh)
```

`grid_carbon_intensity` is resolved one of two ways:

**Dynamic (used whenever an API key is set).** When `JOBCARBON_ELECTRICITY_MAPS_API_KEY` is present, `loader.py` fetches per-hour **direct** carbon intensity from [Electricity Maps][electricitymaps] (`/v3/carbon-intensity/past-range`) over the job's time window, for the configured zone (`JOBCARBON_ELECTRICITY_MAPS_ZONE`, default `US-NE-ISNE`). The series is expanded to `step_seconds` resolution by nearest-neighbour assignment and becomes a per-timestep `grid_carbon_intensity` field on each observation, so operational carbon tracks the grid's actual variation across hours and seasons. Windows longer than the API's per-granularity range limit are fetched in chunks. `direct` (operational combustion) intensity is used rather than `lifecycle`: SCI operational carbon measures emissions caused by the job's electricity draw, not upstream fuel extraction or plant construction.

**Static fallback.** When no API key is set — or the lookup fails or returns no data for the window — a single scalar of **381 gCO2eq/kWh** is injected into `defaults`. This is the annual-average CO2-equivalent emission rate for the Rhode Island grid ([EPA eGRID][egrid2022], 840 lb CO2eq/MWh), overridable via `JOBCARBON_GRID_CARBON_INTENSITY`. A static annual average erases temporal variation, so the dynamic path is preferred when a key is available.

## 4. Per-Component Power Attribution

Each Scaphandre power domain metric reports whole-component power — not the job's share. Each domain is scaled independently by the job's reservation share of the resource that domain measures before the attributed values are summed to produce `node_power_kw`.

### CPU attribution

```
cpu_share            = cpu_allocated / cpu_total
attributed_cpu_power = cpu_power × cpu_share
```

### DRAM attribution (when DRAM domain metric is present)

```
mem_share              = mem_allocated / mem_total
attributed_dram_power  = dram_power × mem_share
```

### GPU attribution (when GPU metric is present)

GPU power is already filtered to the job's assigned GPUs by the PromQL query (`jobid` label). On Oscar, MIG is not used, so each GPU is either fully assigned to a job or not assigned at all. No further scaling is applied.

```
attributed_gpu_power = gpu_power   (sum of job's assigned GPUs, directly from Prometheus)
```

### Node power

```
node_power_kw = attributed_cpu_power
              + attributed_dram_power   (if DRAM domain present)
              + attributed_gpu_power    (if GPU present)
```

### Pipeline variants

The Impact Framework pipeline for each node is assembled from whichever steps are applicable:

| Available domains | Pipeline steps |
|---|---|
| CPU only | `cpu-share → scale-cpu-power → sum-attributed-power` |
| CPU + DRAM | `cpu-share → scale-cpu-power → mem-share → scale-dram-power → sum-attributed-power-dram` |
| CPU + GPU | `cpu-share → scale-cpu-power → sum-attributed-power → sum-gpu-power` |
| CPU + DRAM + GPU | `cpu-share → scale-cpu-power → mem-share → scale-dram-power → sum-attributed-power-dram → sum-gpu-power` |

All variants feed into `duration-to-hours → calculate-energy → calculate-carbon-operational`.

### Empirical basis

All 104 Oscar compute nodes export `scaph_socket_power_microwatts` (CPU domain). A subset also export `scaph_domain_power_microwatts{domain_name="dram"}` (DRAM domain). No node exports DRAM domain data without also exporting CPU domain data. The absence of CPU domain data for a discovered node is treated as a hard error.

## 5. Embodied Carbon (`--embodied`)

Embodied carbon estimation is opt-in via the `--embodied` flag. When not specified, `carbon_operational` is the terminal output and no embodied steps are run.

### CPU and DRAM Embodied Carbon

CPU and DRAM embodied carbon are estimated bottom-up from semiconductor die area using the BoaviztAPI manufacturing model ([de Rancourt et al.][boavizta]). This replaces the earlier `SciEmbodied` plugin (a cloud-VM regression) with an arithmetic chain whose every step is visible in the manifest. The two per-interval outputs `cpu_embodied_carbon` and `dram_embodied_carbon` are summed into `carbon_embodied`.

All constants are stored in gCO2eq and cm² to match the GPU pipeline's `die_area_sq_cm` convention.

**CPU** — die area comes from `config.toml` (`[[cpus]]`, keyed by CPU model → node list); `socket_count` is auto-discovered from Scaphandre (distinct `socket_id` labels), so it never drifts:

```
cpu_embodied_carbon_per_socket = die_area_sq_cm × cpu_die_scalar + cpu_base_carbon   # one CPU, lifetime
cpu_embodied_carbon_node       = cpu_embodied_carbon_per_socket × socket_count        # all sockets
cpu_embodied_carbon_node      ×= cpu_embodied_share                                   # cpu_allocated / cpu_total
cpu_embodied_carbon_per_second = cpu_embodied_carbon_node / cpu_lifespan_seconds
cpu_embodied_carbon            = cpu_embodied_carbon_per_second × duration            # this timestep
```

**DRAM** — die area is derived from installed capacity (`mem_total`) and a fixed die density; the fixed base is applied once (single scalar, not per DIMM):

```
dram_die_area_sq_cm       = mem_total / mem_density_gb_per_sq_cm
dram_embodied_carbon_node = dram_die_area_sq_cm × dram_die_scalar + dram_base_carbon  # lifetime
dram_embodied_carbon_node ×= mem_embodied_share                                       # mem_allocated / mem_total
dram_embodied_carbon_per_second = dram_embodied_carbon_node / cpu_lifespan_seconds
dram_embodied_carbon      = dram_embodied_carbon_per_second × duration                # this timestep
```

**Constants** (`config.py`; injected into the manifest `defaults` so the arithmetic is auditable):

| Constant | Value | Meaning |
|---|---|---|
| `cpu_die_scalar` | 1970 gCO2eq/cm² | CPU die manufacturing (BoaviztAPI 1.97 kgCO2eq/cm²) |
| `cpu_base_carbon` | 9140 gCO2eq | Fixed carbon per CPU (BoaviztAPI 9.14 kgCO2eq) |
| `dram_die_scalar` | 2200 gCO2eq/cm² | DRAM die manufacturing (BoaviztAPI 2.20 kgCO2eq/cm²) |
| `dram_base_carbon` | 5220 gCO2eq | Fixed carbon per RAM component (BoaviztAPI 5.22 kgCO2eq) |
| `mem_density_gb_per_sq_cm` | 1.79 | RAM die density; overridable via `mem_density_gb_per_sq_cm` or `JOBCARBON_MEM_DENSITY_GB_PER_SQ_CM` |

**Attribution:** the embodied chain computes its own `cpu_embodied_share` and `mem_embodied_share` (independent of the operational `cpu_share`/`mem_share`), keeping the operational pipeline untouched.

**Per-timestep amortization:** both node lifetime totals are divided by `cpu_lifespan_seconds` to a per-second rate, then multiplied by `duration`. Summed over the job this equals the job's proportional share of the hardware's lifetime embodied carbon. `cpu_lifespan_seconds` defaults to 5 years, configurable via `cpu_lifespan_years` or `JOBCARBON_CPU_LIFESPAN_YEARS`.

**Die area** (`[[cpus]].die_area_sq_cm`, per socket) is total silicon per socket: Intel monolithic die (LCC/HCC/XCC by core count), AMD summed CCDs + I/O die (+ 3D V-Cache for Genoa-X). Die area is a hardware fact rather than site config, so the model→die-area catalog, derivation method, and sources are maintained as a shared reference in [`docs/die-areas.md`](docs/die-areas.md); each site supplies only the per-node `[[cpus]]` node lists.

**Failure policy:** a node with no `[[cpus]]` entry raises a `ValueError` at manifest generation naming the node. No silent fleet-average fallback.

**Limitations:**

- BoaviztAPI's flat `cpu_die_scalar` assumes a leading-edge logic node. AMD I/O dies (GF 14nm / TSMC N6) are far cheaper per cm² than that, so summing them into `die_area_sq_cm` overcounts I/O-die carbon.
- The DRAM base term is applied once per node rather than per DIMM (following the formula as written), slightly under-counting the fixed term on multi-DIMM nodes; the die term dominates.
- `mem_total` (GiB, from Slurm) is treated as GB against the cm²-based density — a ~7% approximation folded into the estimate.

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

**No separate yield correction**

The Boakes figures below are already yield-corrected. Their functional unit is expressed "per wafer, cm², or die, which considers the functional area (taking die yield and placement into account)" — i.e. total wafer carbon allocated over *good* die area, with the paper's line (90%), die (86%), and cut (72%) yields baked in. The per-cm² scalar is used as published; no additional yield factor is applied (an earlier `/0.9` step double-counted yield and has been removed).

One residual bias remains. The paper's per-cm² figure is calibrated on a 100 mm² (10×10 mm) reference die. GPU compute dies are 6–8× larger (≈600–850 mm²), and Boakes et al. Fig. 10 shows total emissions per die rise *super-linearly* with area because Murphy die-yield collapses on large dies. A flat per-cm² scalar therefore **underestimates** large GPU dies, more so the bigger the die — estimates should be read as conservative lower bounds. Correcting this properly requires running the Murphy yield model at each GPU's actual die area, which is deliberately not done here.

**Manufacturing process scalar** (resolved from `process` in `config/jobcarbon.toml`):

Scalars are the per-process-node functional-unit GWP figures (gCO2eq/cm²) from Boakes et al., "Cradle-to-gate life cycle assessment of CMOS logic technologies," [IEEE IEDM 2023][boakes2023] (Fig. 5/7). The paper covers TSMC nodes N28 through A14.

When multiple EUV/non-EUV variants exist for the same nominal node, the lower (less carbon-intensive) figure is used as a conservative estimate.

| `process_nm` | TSMC node | `process_scalar_carbon_per_sq_cm` (gCO2eq/cm²) |
|---|---|---|
| 28 | N28 | 1380 |
| 20 | N20 | 1470 |
| 14 | N14 | 1550 |
| 10 | N10 | 1780 |
| 7 | N7 (non-EUV) / N7EUV lower | 2060 |
| 5 | N5 | 2420 |
| 4 | N3 (closest documented) | 2740 |
| 3 | N3 | 2740 |
| 2 | N2 | 2730 |

**Samsung 8N exception:** GA102-based GPUs on Oscar (RTX 3090, A5000, A5500, A40, A6000 (Ampere), A2) are manufactured on Samsung 8N. Samsung 8N is not covered by Boakes et al., which is TSMC-specific. TSMC N7 (scalar 2060 gCO2eq/cm²) is used as a conservative proxy — Samsung 8N is a derivative of N10/N7-class lithography and N7 is the closest documented analogue in the conservative direction. These entries are flagged `process: samsung-8n` in `config/jobcarbon.toml`; `config.py` maps `samsung-8n` → the N7 scalar with a logged warning.

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
carbon_embodied (gCO2eq) = cpu_embodied_carbon + dram_embodied_carbon + gpu_embodied_carbon   # GPU profiles
carbon_embodied (gCO2eq) = cpu_embodied_carbon + dram_embodied_carbon                          # non-GPU profiles
carbon          (gCO2eq) = carbon_operational + carbon_embodied
```

Both are reported in `tree.children.<node>.aggregated` in the `if-run` output, summed across all nodes in the job

No normalization denominator is applied. For cross-job comparison on a per-resource-unit basis (e.g. per GPU-hour, per unit of scientific output), users should apply normalization externally

[if-spec]: https://if.greensoftware.foundation/
[egrid2022]: https://www.epa.gov/egrid/detailed-data
[electricitymaps]: https://www.electricitymaps.com/
[a100-lca]: https://arxiv.org/abs/2509.00093
[h100-pcf]: https://images.nvidia.com/aem-dam/Solutions/documents/HGX-H100-PCF-Summary.pdf
[b200-pcf]: https://images.nvidia.com/aem-dam/Solutions/documents/HGX-B200-PCF-Summary.pdf
[boakes2023]: https://ieeexplore.ieee.org/document/10413725
[boavizta]: https://boavizta.org/
[hotcarbon2024]: https://hotcarbon.org/assets/2024/pdf/hotcarbon24-final154.pdf
[techpowerup]: https://www.techpowerup.com/gpu-specs/
[tpu-tu102]: https://www.techpowerup.com/gpu-specs/nvidia-tu102.g813
[tpu-ga102]: https://www.techpowerup.com/gpu-specs/nvidia-ga102.g930
[tpu-ga107]: https://www.techpowerup.com/gpu-specs/nvidia-ga107.g988
[tpu-ad102]: https://www.techpowerup.com/gpu-specs/nvidia-ad102.g1005
[cnc-h100]: https://chipsandcheese.com/p/nvidias-h100-funny-l2-and-tons-of-bandwidth
[cnc-blackwell]: https://chipsandcheese.com/p/blackwell-nvidias-massive-gpu
