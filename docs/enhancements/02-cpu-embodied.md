---
title: CPU and DRAM Embodied Carbon (Bottom-Up)
status: proposed
owners: [@broarr]
created: 2026-05-07
updated: 2026-05-07
---

# CPU and DRAM Embodied Carbon — Bottom-Up Estimation (PRD)

Goal: replace the `SciEmbodied` builtin plugin (calibrated for cloud VMs) with a bottom-up,
die-area-based arithmetic pipeline — consistent with the existing GPU embodied carbon approach —
so that server embodied carbon is grounded in physical semiconductor data rather than a
cloud-VM regression.

## Summary

- Remove `SciEmbodied` from all six pipeline templates.
- Replace it with `Multiply`/`Coefficient`/`Sum` steps that compute CPU chip embodied carbon
  from die area × process scalar (same Boakes et al. scalars used for GPUs), DRAM embodied
  carbon from allocated memory × a DDR scalar, and a fixed chassis overhead, all amortized by
  allocation share and job duration.
- Add a `[[cpus]]` section to `config/jobcarbon.toml` (analogous to `[[gpus]]`) mapping node
  hostnames to CPU hardware specs.
- Add `"ddr4"`, `"ddr5"`, `"intel-14nm"`, and `"intel-10nm"` entries to the scalar tables in
  `jobconfig.py`.
- Fail fast if a node's hostname has no `[[cpus]]` entry at manifest generation time.

## Motivation

`SciEmbodied` estimates embodied carbon by starting from a "baseline server" carbon figure
(default: 1,000,000 gCO2eq) and adding a marginal increment per vCPU and per GB above that
baseline. The defaults were tuned for cloud VMs. On bare-metal HPC nodes `cpu_allocated` can
equal the full physical core count, which drives the formula far above single-server embodied
carbon. No PCF data is publicly available (or is NDA'd) for Oscar's specific server SKUs.

The GPU pipeline already demonstrates the correct approach: derive embodied carbon from
first-principles semiconductor data using Boakes et al. IEDM 2023 scalars. Applying the same
method to CPUs produces a methodology that is consistent, defensible, fully visible in the
manifest, and free of PCF dependencies.

## Scope

- Replace `sci-m: SciEmbodied` in all six templates with a bottom-up arithmetic chain.
- Inject `cpu_chip_carbon_per_socket`, `cpu_socket_count`, `dram_scalar_gco2_per_gb`, and
  `chassis_gco2eq` into each node's `defaults` block via the generator.
- Add `[[cpus]]` config parsing to `jobconfig.py` and `Config`.
- Add `"ddr4"`, `"ddr5"` to `MEM_SCALARS`; add `"intel-14nm"`, `"intel-10nm"` to
  `PROCESS_SCALARS`.
- Update `METHODOLOGY.md §5` to retire the `SciEmbodied` description and document the new
  approach.

## Out of Scope (MVP)

- Automatically discovering CPU model from Prometheus — no such metric exists. Node→CPU mapping
  is maintained out-of-band in `config/jobcarbon.toml`.
- Per-generation chassis carbon differentiation. A single chassis constant per `[[cpus]]` entry.
- Motherboard, PSU, or NIC embodied carbon broken out separately (subsumed into chassis).
- Storage embodied carbon (out of scope per METHODOLOGY.md §1).

## Design Decisions

### Scalar computation in Python, not in the pipeline

For AMD Genoa-X (EPYC 9684X), each socket has three distinct die types: 12 Zen 4 CCDs on
TSMC N5, 12 V-Cache SRAM chiplets on TSMC N6, and 1 I/O die on TSMC N6. Representing this
as per-die-type pipeline steps would add 5–10 steps per template and create profile-specific
branching.

Instead, `_cpu_defaults()` in `generator.py` sums chip carbon across all die types within a
socket and injects a single `cpu_chip_carbon_per_socket` scalar. Templates are uniform across
all CPU types. The per-socket scalar and its derivation are auditable via METHODOLOGY.md and
the `[[cpus]]` TOML entry.

### `[[cpus]]` config structure

```toml
[[cpus]]
cpu_model      = "Intel Xeon Platinum 8268 (Cascade Lake-SP)"
socket_count   = 2
mem_type       = "ddr4"
chassis_gco2eq = 200000
nodes          = ["node1802", "node1804", ...]

  [[cpus.dies]]
  process      = "intel-14nm"
  die_area_cm2 = 6.94
  count        = 1          # per socket; multiplied by socket_count by the generator
```

For AMD Genoa-X (multi-die package):

```toml
[[cpus]]
cpu_model      = "AMD EPYC 9684X (Genoa-X, 96-core)"
socket_count   = 2
mem_type       = "ddr5"
chassis_gco2eq = 200000
nodes          = ["node2353", ...]

  [[cpus.dies]]
  process      = "tsmc-n5"
  die_area_cm2 = 0.72    # Zen 4 CCD; third-party die measurement
  count        = 12       # per socket

  [[cpus.dies]]
  process      = "tsmc-n6"
  die_area_cm2 = 0.41    # V-Cache SRAM chiplet; third-party die measurement
  count        = 12       # per socket, one per CCD

  [[cpus.dies]]
  process      = "tsmc-n6"
  die_area_cm2 = 4.06    # I/O die; third-party die measurement
  count        = 1        # per socket
```

### `cpu_chip_carbon_per_socket` formula

```
cpu_chip_carbon_per_socket = sum(
    die.die_area_cm2 × die.count × PROCESS_SCALARS[die.process]
    for die in entry.dies
)
```

### New process scalars for Intel nodes

`PROCESS_SCALARS` currently covers TSMC and Samsung only. Intel nodes use proxies, with a
logged warning at config load time (same pattern as `samsung-8n`):

| Key | Proxy | Scalar (gCO2eq/cm²) | Rationale |
|---|---|---|---|
| `intel-14nm` | TSMC N10 | 1780 | Intel 14nm++ transistor density is closest to TSMC N10 in published comparisons |
| `intel-10nm` | TSMC N7 | 2220 | Intel 10nm SuperFin density benchmarks align with TSMC N7 |

### New DRAM scalars

| Key | gCO2eq/GB | Source |
|---|---|---|
| `ddr4` | 10 | Li, Graif, Gupta, HotCarbon 2024 (Table 1) |
| `ddr5` | 12 | Conservative estimate; DDR5 uses more advanced process nodes; consistent with Li et al. methodology |

### Chassis constant

`chassis_gco2eq` covers the server chassis, motherboard, PSU, and associated components.
Default: **200,000 gCO2eq** (~200 kg CO2eq), derived from Gupta et al. ACT 2022 and
iFixit/TechInsights teardown analyses for 1U/2U rack servers. Operators may override per entry.

### New pipeline steps (all six templates)

The single `sci-m: SciEmbodied` block is replaced by:

```
cpu-chip-embodied-total     Multiply: cpu_chip_carbon_per_socket × cpu_socket_count
                             → cpu_chip_carbon_node  (lifetime, gCO2eq)

dram-embodied               Multiply: mem_allocated × dram_scalar_gco2_per_gb
                             → dram_carbon  (lifetime, gCO2eq)

server-raw-embodied         Sum: [cpu_chip_carbon_node, dram_carbon, chassis_gco2eq]
                             → server_embodied_raw  (lifetime, gCO2eq)

allocation-share            Divide: cpu_allocated / cpu_total  → allocation_share
                             [already present in host_only templates; added to full templates]

server-embodied-allocated   Multiply: server_embodied_raw × allocation_share
                             → server_embodied_allocated  (lifetime, gCO2eq)

sci-m                       Multiply: server_embodied_allocated × (duration / cpu_lifespan_seconds)
                             → carbon_embodied  (this timestep, gCO2eq)
```

The output field `carbon_embodied` is preserved. `sum-carbon` and aggregation config are unchanged.

In GPU templates, this chain replaces `server-embodied: SciEmbodied`.

### Failure policy

If a node has no `[[cpus]]` entry at manifest generation time, generation fails with a
`ValueError` naming the node. Same policy as GPU nodes. No silent fallback.

## Known Hardware on Oscar

### Non-GPU nodes

| Feature tag | CPU model | Sockets | Process | Notes |
|---|---|---|---|---|
| `cascade` (32c) | **Unknown — need `lscpu`** | TBD | `intel-14nm` | node[1347-1364,1601-1608,1609-1656,1701-1756] |
| `cascade` (48c) | Intel Xeon Platinum 8268 | 2 × 24c | `intel-14nm` | node[1802,1804-1856,1904-1956,2301-2352]; confirmed |
| `skylake` (24c) | **Unknown SKU — verify with `lscpu`** | ~2 × 12c | `intel-14nm` | node[1317-1328] |
| `icelake` (64c) | Ice Lake-SP | 2 × 32c | `intel-10nm` | node[2417-2420] |
| `genoa` (192c) | AMD EPYC 9684X | 2 × 96c | N5+N6+N6 | node[2353-2356,2405-2416,2421-2432]; confirmed |

### Die area data (per socket)

| CPU | Die | Process key | Area (cm²) | Count/socket | Source |
|---|---|---|---|---|---|
| Cascade Lake-SP (8268) | HCC monolithic | `intel-14nm` | 6.94 | 1 | Wikichip / Fritzchens Fritz die shots |
| Skylake-SP HCC | HCC monolithic | `intel-14nm` | 6.98 | 1 | Wikichip |
| Ice Lake-SP | Monolithic | `intel-10nm` | 6.60 | 1 | Wikichip |
| EPYC 9684X — CCD | Zen 4 CCD | `tsmc-n5` | 0.72 | 12 | TechInsights / SemiAnalysis |
| EPYC 9684X — V-Cache | SRAM chiplet | `tsmc-n6` | 0.41 | 12 | SemiAnalysis (Genoa-X launch) |
| EPYC 9684X — IOD | I/O die | `tsmc-n6` | 4.06 | 1 | SemiAnalysis |

All figures are third-party measurements; neither Intel nor AMD publishes official die areas.

## Files Changed

| File | Change |
|---|---|
| `src/jobconfig.py` | Add `"ddr4"`, `"ddr5"` to `MEM_SCALARS`; add `"intel-14nm"`, `"intel-10nm"` to `PROCESS_SCALARS` with warning; add `[[cpus]]` parsing to `Config.load()`; add `cpu_for_node()` |
| `src/generator.py` | Add `_cpu_defaults()`: iterates die specs, computes `cpu_chip_carbon_per_socket`, returns defaults dict; include in `_build_node()` |
| `templates/full.yaml` | Replace `sci-m: SciEmbodied` with 6-step chain; add `allocation-share` and `server-embodied-allocated` |
| `templates/host_only.yaml` | Replace `sci-m: SciEmbodied`; reuse existing `allocation-share`; add remaining steps |
| `templates/full_gpu_estimated.yaml` | Replace `server-embodied: SciEmbodied` with arithmetic chain |
| `templates/host_only_gpu_estimated.yaml` | Same |
| `templates/full_gpu_pcf.yaml` | Same |
| `templates/host_only_gpu_pcf.yaml` | Same |
| `config/jobcarbon.toml` | Add `[[cpus]]` sections (32-core cascade and skylake pending `lscpu` verification) |
| `tests/test_jobconfig.py` | Test `[[cpus]]` parsing, `cpu_for_node()`, multi-die scalar computation, Intel proxy warnings, duplicate node detection |
| `tests/test_generator.py` | Assert required fields in `defaults`; assert `ValueError` on unmapped node |
| `METHODOLOGY.md` | Update §5: retire `SciEmbodied` description; document new formula, die area table, Intel proxies, DDR scalars, chassis constant, limitations |

## Validation and Acceptance Criteria

### Example arithmetic (Cascade Lake-SP, 2× Platinum 8268)

Given: 2 sockets, 6.94 cm² die, `intel-14nm` (1780 gCO2eq/cm²), `ddr4` (10 gCO2eq/GB),
chassis 200,000 gCO2eq, 24 cpu_allocated / 48 cpu_total, 16 GiB mem_allocated:

```
cpu_chip_carbon_per_socket = 6.94 × 1 × 1780              = 12,353.2 gCO2eq
cpu_chip_carbon_node       = 12,353.2 × 2                  = 24,706.4 gCO2eq
dram_carbon                = 16 × 10                       =    160.0 gCO2eq
server_embodied_raw        = 24,706.4 + 160 + 200,000      = 224,866.4 gCO2eq
allocation_share           = 24 / 48                       =       0.5
server_embodied_allocated  = 224,866.4 × 0.5               = 112,433.2 gCO2eq
carbon_embodied (60s)      = 112,433.2 × (60/157,680,000) ≈     0.0428 gCO2eq
```

### Example arithmetic (AMD EPYC 9684X, 2 sockets)

Given per-socket die specs as above, `ddr5` (12 gCO2eq/GB):

```
CCD carbon/socket     = 0.72 × 12 × 2420  = 20,908.8 gCO2eq
V-Cache carbon/socket = 0.41 × 12 × 1550  =  7,626.0 gCO2eq
IOD carbon/socket     = 4.06 ×  1 × 1550  =  6,293.0 gCO2eq
cpu_chip_carbon_per_socket                 = 34,827.8 gCO2eq
```

Note: `tsmc-n6` is not currently in `PROCESS_SCALARS`. It will be added at implementation
time, either as an interpolated value between N7 (2220) and N5 (2420), or as an alias for
N7 as a conservative proxy — to be decided during implementation.

### Acceptance criteria

- All six templates produce `carbon_embodied` without any `SciEmbodied` / `vCPUs` reference.
- Generated manifests contain `cpu_chip_carbon_per_socket`, `cpu_socket_count`,
  `dram_scalar_gco2_per_gb`, `chassis_gco2eq` in `defaults`.
- `carbon_embodied` summed over all timesteps equals
  `server_embodied_allocated × job_duration / cpu_lifespan_seconds`.
- Unmapped node raises `ValueError` with node name in message.
- All existing tests continue to pass.

## Open Questions / Future Work

- **32-core cascade and skylake CPU models**: run `lscpu` on one node in each range before
  implementation to confirm socket count and SKU.
- **`tsmc-n6` scalar**: determine whether to interpolate between N7/N5 or use N7 as a
  conservative proxy. Affects Genoa-X V-Cache and IOD carbon estimates.
- **Chassis constant**: 200,000 gCO2eq is a rough estimate. If a teardown LCA for any of
  Oscar's server models becomes available, update the relevant `[[cpus]]` entry.
- **V-Cache die area**: the 41 mm² figure is a reverse-engineering estimate. Update if AMD or
  TechInsights publishes a verified figure.
- **`intel-14nm` / `intel-10nm` scalars**: replace proxies when Boakes et al. or equivalent
  covers Intel fab GWP data.
- **DDR5 scalar**: replace conservative estimate if Li et al. or a follow-up study publishes
  an empirical DDR5 value.

## References

- Boakes et al., "Cradle-to-gate life cycle assessment of CMOS logic technologies," IEEE IEDM
  2023 — source for `PROCESS_SCALARS`
- Li, Graif, Gupta, "Towards Carbon-efficient LLM Life Cycle," HotCarbon 2024 — source for
  DDR4/DDR5 `MEM_SCALARS`
- Gupta et al., "ACT: Designing Sustainable Computer Systems with an Architectural Carbon
  Modeling Tool," ASPLOS 2022 — chassis constant methodology reference
- Wikichip — Intel Cascade Lake-SP, Skylake-SP, Ice Lake-SP die area figures
- SemiAnalysis / TechInsights — AMD Genoa CCD, V-Cache, IOD die area figures
- AMD Hot Chips 35 (2023) — Genoa-X V-Cache SRAM chiplet on TSMC N6 (process node confirmed)
