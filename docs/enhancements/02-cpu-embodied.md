---
title: CPU and DRAM Embodied Carbon (BoaviztAPI Bottom-Up)
status: proposed
owners: [@broarr]
created: 2026-05-07
updated: 2026-07-27
---

# CPU and DRAM Embodied Carbon — BoaviztAPI Bottom-Up (PRD)

Goal: replace the `SciEmbodied` builtin plugin (a cloud-VM regression) with a bottom-up
arithmetic pipeline that estimates CPU and DRAM embodied carbon from die area, using the
BoaviztAPI manufacturing model. Consistent with the existing GPU embodied pipeline: raw
parameters and resolved constants are injected into the manifest `defaults`, and all
arithmetic runs as visible IF pipeline steps.

> **Supersedes** the earlier draft of this PRD, which used per-process-node Boakes et al.
> scalars, chiplet die breakdowns, and Intel-proxy die scalars. BoaviztAPI uses a single flat
> die scalar plus a fixed base constant per component — simpler, fewer config knobs, no
> per-process table, no chiplet accounting, and no Intel-proxy guesswork.

## Summary

- Remove `server-embodied.yaml` (`SciEmbodied`) from all three embodied templates.
- Replace it with a `Divide`/`Multiply`/`Sum`/`Coefficient` chain that computes CPU chip
  embodied carbon from die area, DRAM embodied carbon from installed capacity, amortizes both
  over the server lifespan, scales by observation duration, and attributes each by its own
  allocation share.
- Add a `[[cpus]]` section to the config (analogous to `[[gpus]]`) mapping node hostnames to
  CPU/DRAM hardware specs.
- The **operational pipeline is untouched.** All new logic lives in the embodied-only step
  lists, gated behind `config.embodied`.

## Motivation

`SciEmbodied` estimates embodied carbon from a baseline "cloud server" figure plus a marginal
increment per vCPU and per GB. The defaults were tuned for cloud VMs. On bare-metal HPC nodes
`cpu_allocated` can equal the full physical core count, which drives the formula far above
real single-server embodied carbon. No PCF data is publicly available for Oscar's server SKUs.

The GPU pipeline already demonstrates the intended approach: derive embodied carbon from
first-principles semiconductor die area. BoaviztAPI ("BoaviztAPI: A Bottom-Up Model to Assess
the Environmental Impacts of Cloud Services") gives a simple, well-sourced die-area model for
CPU and RAM that fits this pattern directly.

## Formulas (BoaviztAPI)

All constants are stored in **gCO2eq** and **cm²** to match the existing `die_area_sq_cm` and
`process_scalars` conventions.

### CPU

```
F^e_cpu = (die_cpu · F^die_cpu + I^base_cpu) / D
```

| Symbol | Meaning | Value |
|---|---|---|
| `die_cpu` | CPU die area per socket | from config, cm² |
| `F^die_cpu` | die manufacturing scalar | 1.97 kgCO2eq/cm² = **1970 gCO2eq/cm²** |
| `I^base_cpu` | fixed base per CPU | 9.14 kgCO2eq = **9140 gCO2eq** |
| `D` | life expectancy | `cpu_lifespan_seconds` (existing config) |

The `/ D` amortization is done in the pipeline as `× 1/cpu_lifespan_seconds × duration`, so the
plugin chain produces lifetime carbon first, then scales to the observation interval.

### DRAM

```
F^e_ram = ((capacity / density) · F^die_ram + I^base_ram) / D
```

`capacity / density` **is** die area in cm². `capacity` is `mem_total` (GB; Slurm MB ÷ 1024 —
GiB treated as GB, a <7% approximation folded into the estimate).

| Symbol | Meaning | Value |
|---|---|---|
| `capacity` | installed RAM | `mem_total`, GB |
| `density` | die density | 1.79 GB/cm² (`mem_density_gb_per_sq_cm`) |
| `F^die_ram` | die manufacturing scalar | 2.20 kgCO2eq/cm² = **2200 gCO2eq/cm²** |
| `I^base_ram` | fixed base **per module** | 5.22 kgCO2eq = **5220 gCO2eq** × `dram_module_count` |
| `D` | life expectancy | `cpu_lifespan_seconds` (RAM shares server lifespan) |

## Design Decisions

### Attribution: embodied chain computes its own shares

CPU embodied carbon is attributed by `cpu_allocated / cpu_total`; DRAM by
`mem_allocated / mem_total`. The operational pipeline already computes `cpu_share` and (on DRAM
nodes) `mem_share`, **but the embodied chain does not reuse them.** Reusing them would couple
the two pipelines and break on nodes where `mem_share` isn't computed operationally. Instead the
embodied chain computes its own shares under distinct output names (`cpu_embodied_share`,
`mem_embodied_share`). Two extra `Divide` steps; zero coupling; no conditional step insertion.

### `[[cpus]]` config structure

Keyed by CPU model, listing the nodes that carry it — same inversion pattern as `[[gpus]]`.
Adding a homogeneous rack is one entry, not N.

```toml
# module-level constant
mem_density_gb_per_sq_cm = 1.79   # BoaviztAPI RAM die density, GB/cm²

[[cpus]]
cpu_model         = "Intel Xeon Platinum 8268 (Cascade Lake-SP)"
die_area_sq_cm    = 6.94          # total die area per socket, from TechPowerUp
socket_count      = 2
dram_module_count = 8             # installed DIMM count (base term is per module)
nodes             = ["node1802", "node1804"]
```

`die_area_sq_cm` is the CPU's total die area (for chiplet parts, the summed die area of the
package) — a single number from TechPowerUp per CPU family. No per-die breakdown.

### Constants in code, not per-entry

`CPU_DIE_SCALAR` (1970), `CPU_BASE` (9140), `DRAM_DIE_SCALAR` (2200), `DRAM_BASE` (5220), and
`DEFAULT_MEM_DENSITY` (1.79) are module constants in `config.py`, overridable only via the
top-level `mem_density_gb_per_sq_cm` config key (the one most likely to need tuning). They are
injected into `defaults` so the manifest stays self-documenting.

### No auto-discovery of CPU model

CPU model is not in Slurm GRES (unlike GPUs), so `jobcarbon-create-config` cannot populate
`[[cpus]]`. It emits a single commented placeholder entry. Node→CPU mapping is maintained
out-of-band by operators.

### Failure policy

If a node has no `[[cpus]]` entry at manifest generation time, generation fails with a
`ValueError` naming the node. Same policy as GPU nodes. No silent fleet-average fallback.

## Pipeline

`server-embodied.yaml` (`SciEmbodied`) is deleted. It is replaced in each of the three
`EMBODIED_STEPS_*` lists by this ordered chain (embodied-only — appended after the untouched
operational steps when `--embodied` is set):

**Shares**

| step | method | output |
|---|---|---|
| `cpu-embodied-share` | Divide `cpu_allocated / cpu_total` | `cpu_embodied_share` |
| `mem-embodied-share` | Divide `mem_allocated / mem_total` | `mem_embodied_share` |

**CPU**

| step | method | output |
|---|---|---|
| `cpu-die-embodied` | Multiply `die_area_sq_cm × cpu_die_scalar` | `cpu_die_carbon` |
| `cpu-embodied-per-socket` | Sum `[cpu_die_carbon, cpu_base_carbon]` | `cpu_embodied_per_socket` |
| `cpu-embodied-node` | Multiply `× socket_count` | `cpu_embodied_carbon_node` (lifetime) |
| `cpu-embodied-per-second` | Coefficient `× 1/cpu_lifespan_seconds` | `cpu_embodied_carbon_per_second` |
| `cpu-embodied-time-scale` | Multiply `× duration` | `cpu_embodied_carbon_scaled` |
| `cpu-embodied-attributed` | Multiply `× cpu_embodied_share` | `cpu_embodied_carbon` |

**DRAM**

| step | method | output |
|---|---|---|
| `dram-die-area` | Divide `mem_total / mem_density_gb_per_sq_cm` | `dram_die_area_sq_cm` |
| `dram-die-embodied` | Multiply `× dram_die_scalar` | `dram_die_carbon` |
| `dram-base-embodied` | Multiply `dram_base_carbon × dram_module_count` | `dram_base_total` |
| `dram-embodied-node` | Sum `[dram_die_carbon, dram_base_total]` | `dram_embodied_carbon_node` (lifetime) |
| `dram-embodied-per-second` | Coefficient `× 1/cpu_lifespan_seconds` | `dram_embodied_carbon_per_second` |
| `dram-embodied-time-scale` | Multiply `× duration` | `dram_embodied_carbon_scaled` |
| `dram-embodied-attributed` | Multiply `× mem_embodied_share` | `dram_embodied_carbon` |

**Sum**

`sum-embodied.yaml` inputs change to `[cpu_embodied_carbon, dram_embodied_carbon]` →
`carbon_embodied`. `sum-carbon` is unchanged. In GPU templates, `sum-embodied-gpu` stacks GPU
embodied on top as today.

## Manifest `defaults` (embodied path)

Injected by `_cpu_defaults()`, parallel to `_gpu_defaults()`:

```yaml
defaults:
  # ... existing operational + lifespan fields
  die_area_sq_cm: 6.94
  socket_count: 2
  dram_module_count: 8
  cpu_die_scalar: 1970
  cpu_base_carbon: 9140
  dram_die_scalar: 2200
  dram_base_carbon: 5220
  mem_density_gb_per_sq_cm: 1.79
```

Both raw parameters and resolved constants appear, so a reader can verify the arithmetic from
the manifest alone.

## Files Changed

| File | Change |
|---|---|
| `src/jobcarbon/config.py` | Add `CpuSpec` TypedDict; `CPU_DIE_SCALAR`/`CPU_BASE`/`DRAM_DIE_SCALAR`/`DRAM_BASE`/`DEFAULT_MEM_DENSITY` constants; parse `[[cpus]]` and `mem_density_gb_per_sq_cm`; `_build_cpu_node_map()` (reuse dup-node guard); `cpu_for_node()`; commented `[[cpus]]` placeholder in `generate()` |
| `src/jobcarbon/generator.py` | Replace `"server-embodied"` in the three `EMBODIED_STEPS_*` lists with the new chain; add `_cpu_defaults()`; wire into `_node_defaults()` under the `config.embodied` gate; raise `ValueError` on unmapped node |
| `src/jobcarbon/plugins/server-embodied.yaml` | Delete |
| `src/jobcarbon/plugins/*.yaml` | Add 2 share + 6 CPU + 7 DRAM plugin files listed above |
| `src/jobcarbon/plugins/sum-embodied.yaml` | Inputs → `[cpu_embodied_carbon, dram_embodied_carbon]` |
| `tests/test_config.py` | `[[cpus]]` parsing, `cpu_for_node()`, duplicate-node `ValueError`, `mem_density` override |
| `tests/test_generator.py` | Assert new `defaults` fields; assert step list; assert `ValueError` on unmapped node |
| `METHODOLOGY.md` | §5: retire `SciEmbodied`; document BoaviztAPI CPU/DRAM formulas, constants, density, module count, limitations |

## Validation

### Example (2× Xeon Platinum 8268, 512 GB, 8 DIMMs)

Given: `die_area_sq_cm = 6.94`, `socket_count = 2`, `mem_total = 512`, `dram_module_count = 8`,
`cpu_lifespan_seconds = 157,680,000` (5 yr), `duration = 60`, `cpu_allocated/cpu_total = 24/48`,
`mem_allocated/mem_total = 128/512`.

```
CPU lifetime:
  cpu_die_carbon          = 6.94 × 1970                 =    13,671.8 gCO2eq
  cpu_embodied_per_socket = 13,671.8 + 9140             =    22,811.8 gCO2eq
  cpu_embodied_node       = 22,811.8 × 2                =    45,623.6 gCO2eq
  per 60s, share 0.5      = 45,623.6 /157,680,000×60×0.5 ≈  0.00868 gCO2eq

DRAM lifetime:
  dram_die_area           = 512 / 1.79                  =    286.03 cm²
  dram_die_carbon         = 286.03 × 2200               =   629,266 gCO2eq
  dram_base_total         = 5220 × 8                    =    41,760 gCO2eq
  dram_embodied_node      = 629,266 + 41,760            =   671,026 gCO2eq
  per 60s, share 0.25     = 671,026 /157,680,000×60×0.25 ≈  0.0638 gCO2eq

carbon_embodied (60s)     = 0.00868 + 0.0638           ≈    0.0725 gCO2eq
```

### Acceptance criteria

- All three embodied templates produce `carbon_embodied` with no `SciEmbodied`/`vCPUs` reference.
- Generated manifests contain the eight `defaults` fields above.
- `carbon_embodied` summed over all timesteps equals
  `(cpu_embodied_node × cpu_share + dram_embodied_node × mem_share) × job_duration / cpu_lifespan_seconds`.
- Unmapped node raises `ValueError` naming the node.
- Operational-only manifests (no `--embodied`) are byte-identical to today.
- All existing tests pass.

## Open Questions / Future Work

- **`mem_density_gb_per_sq_cm`**: 1.79 is the BoaviztAPI default. Revisit if Oscar's DIMMs use a
  known newer node.
- **GiB vs GB**: `mem_total` is GiB treated as GB (~7% low). Correct with a `× 1.024` coefficient
  if warranted.
- **Chiplet CPUs**: `die_area_sq_cm` is the summed package die area; BoaviztAPI's flat scalar does
  not distinguish process nodes across chiplets. Acceptable at this model's fidelity.
- **SSD/NAND embodied**: out of scope per METHODOLOGY.md §1.

## References

- de Rancourt et al., "BoaviztAPI: A Bottom-Up Model to Assess the Environmental Impacts of Cloud
  Services" — source for CPU (1.97, 9.14) and RAM (2.20, 5.22, 1.79 density) constants
- TechPowerUp — CPU die area figures per family
- METHODOLOGY.md §5 — embodied carbon and the retired `SciEmbodied` plugin
- `docs/enhancements/01-gpu-embodied.md` — the parallel GPU embodied pipeline this mirrors
</content>
</invoke>
