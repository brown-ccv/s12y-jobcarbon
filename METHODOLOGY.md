# Methodology

How `jobcarbon` turns Prometheus telemetry into a per-job [Impact Framework][if-spec] manifest. This is a reference for reading the generated arithmetic; the full derivations, source tables, and validation live in the accompanying paper and in [`docs/die-areas.md`](docs/die-areas.md).

## Scope

Per-job estimates of **operational** carbon (compute power draw) and, with `--embodied`, **embodied** carbon of compute hardware (manufacture, amortized over hardware lifetime). The value is comparative — ranking jobs on a common, reproducible scale — not an exact real-world footprint. Network and storage carbon are out of scope (no per-job attribution in the available telemetry) and left as future work.

## Telemetry and attribution

Power is read from Prometheus per node; the pipeline is assembled from whichever domains are present (no fixed profile enum). Each whole-component reading is scaled by the job's reservation share before summing:

| Domain | Metric | Attribution |
|---|---|---|
| CPU | `scaph_socket_power_microwatts` (required; absence is a hard error) | × `cpu_allocated / cpu_total` |
| DRAM | `scaph_domain_power_microwatts{domain_name="dram"}` (subset of nodes) | × `mem_allocated / mem_total` |
| GPU | `nvidia_gpu_power_usage_milliwatts` (GPU nodes) | already filtered to the job's GPUs by `jobid`; no scaling (no MIG) |

```
node_power_kw = attributed_cpu_power [+ attributed_dram_power] [+ attributed_gpu_power]
```

Whole-node `scaph_host_power_microwatts` is not used — it carries no attribution the per-domain metrics don't give more accurately.

## Operational carbon

Per 60-second observation (resolution via `JOBCARBON_STEP_SECONDS`; values are `avg_over_time` over each step):

```
energy (kWh)              = node_power_watts / 1000 × duration_s / 3600
carbon_operational (gCO2eq) = grid_carbon_intensity × energy
```

`energy` aggregates `{time: sum, component: sum}`; `carbon_operational` is derived per-timestep before aggregation, so there is no double-counting. A missing step extends the prior observation's `duration` and reuses its power as an estimate for the gap.

**Grid carbon intensity** — dynamic when `JOBCARBON_ELECTRICITY_MAPS_API_KEY` is set: per-hour **direct** intensity from [Electricity Maps][electricitymaps] for `JOBCARBON_ELECTRICITY_MAPS_ZONE` (default `US-NE-ISNE`), nearest-neighbour expanded to `step_seconds`. Otherwise a static **381 gCO2eq/kWh** (Rhode Island annual average, [EPA eGRID][egrid2022]; override via `JOBCARBON_GRID_CARBON_INTENSITY`).

## Embodied carbon (`--embodied`)

Opt-in. All figures are **manufacturing only (cradle-to-gate)** — use-phase is `carbon_operational`, so a full-lifecycle figure here would double-count. Each node total is amortized to the timestep by `× duration / lifespan_seconds` (CPU/DRAM: `cpu_lifespan_years`, default 5; GPU: `gpu_lifespan_years`). Every intermediate is injected into the manifest `defaults` so the chain is auditable.

**CPU / DRAM** — bottom-up from die area, BoaviztAPI model ([de Rancourt et al.][boavizta]). CPU die area per socket comes from `config.toml` `[[cpus]]`; `socket_count` is auto-discovered from Scaphandre. DRAM die area is derived from `mem_total`.

```
cpu_embodied_node  = (die_area_sq_cm × cpu_die_scalar + cpu_base_carbon) × socket_count × (cpu_allocated / cpu_total)
dram_embodied_node = ((mem_total / mem_density) × dram_die_scalar + dram_base_carbon) × (mem_allocated / mem_total)
```

| Constant | Value | Meaning |
|---|---|---|
| `cpu_die_scalar` | 1970 gCO2eq/cm² | CPU die manufacturing |
| `cpu_base_carbon` | 9140 gCO2eq | fixed carbon per CPU |
| `dram_die_scalar` | 2200 gCO2eq/cm² | DRAM die manufacturing |
| `dram_base_carbon` | 5220 gCO2eq | fixed carbon per RAM component |
| `mem_density_gb_per_sq_cm` | 1.79 | RAM die density |

**GPU** — two paths, selected per model:

- **Directly-sourced figure** — a published cradle-to-gate number, stored in config as `pcf_carbon_per_gpu` (manufacturer PCF) or `lca_carbon_per_gpu` (third-party LCA); provenance is visible in the config but the two are treated identically downstream (`embodied_carbon_per_gpu`):

  ```
  gpu_embodied_node = embodied_carbon_per_gpu × gpu_count
  ```

- **Estimated** — for models without such a figure, from die area and VRAM:

  ```
  gpu_embodied_node = (die_area_sq_cm × process_scalar + vram_gb × mem_scalar) × gpu_count
  ```

  Process scalars (gCO2eq/cm²) are from Boakes et al. ([IEDM 2023][boakes2023], cradle-to-gate, already yield-corrected); memory scalars (gCO2eq/GiB) from Li et al. ([HotCarbon 2024][hotcarbon2024]). Both live in `config.py` (`PROCESS_SCALARS`, `MEM_SCALARS`); per-GPU die areas, foundry nodes, sources, and proxy choices (e.g. Samsung 8N → TSMC N7) are in [`docs/die-areas.md`](docs/die-areas.md). A flat per-cm² scalar underestimates large GPU dies, so estimates read as conservative lower bounds.

**Failure policy:** a GPU or CPU node with no matching config entry fails manifest generation with a named error — no silent fleet-average fallback.

## Output

```
carbon_operational                                   # default (terminal output)
carbon_embodied = cpu + dram [+ gpu] embodied        # with --embodied
carbon          = carbon_operational + carbon_embodied
```

Reported per node in `tree.children.<node>.aggregated`, summed across the job. No normalization is applied; normalize externally for per-GPU-hour or per-output comparison.

[if-spec]: https://if.greensoftware.foundation/
[egrid2022]: https://www.epa.gov/egrid/detailed-data
[electricitymaps]: https://www.electricitymaps.com/
[boavizta]: https://boavizta.org/
[boakes2023]: https://ieeexplore.ieee.org/document/10413725
[hotcarbon2024]: https://hotcarbon.org/assets/2024/pdf/hotcarbon24-final154.pdf
