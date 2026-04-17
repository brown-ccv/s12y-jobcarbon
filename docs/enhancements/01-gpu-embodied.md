---
title: GPU Embodied Carbon
status: proposed
owners: [@broarr]
created: 2026-04-16
updated: 2026-04-27
---

# GPU Embodied Carbon (PRD)

Goal: close the known gap in `jobcarbon` where GPU embodied carbon is absent, producing reproducible, auditable, and schema-compatible manifests that allow per-job SCI results to include GPU manufacturing impact.

This document specifies the product requirements, data model, manifest and pipeline changes, operational behaviour, and acceptance criteria for adding GPU embodied carbon to `jobcarbon`.

## Summary

- Add per-GPU embodied carbon into Impact Framework manifests used by `jobcarbon`.
- Manifests remain self-documenting: raw GPU parameters (`die_area_cm2`, `vram_gb`, resolved scalars) are injected into the manifest `defaults` block, and the arithmetic runs inside the IF pipeline. The methodology is fully encoded in the pipeline steps visible in the manifest.
- Use a TOML file (`config/gpu_embodied.toml`) as the single source-of-truth (SoT). Entries are keyed by GPU card model; each entry lists the node hostnames that carry that card. The generator inverts this at load time to a `node → gpu_entry` map.
- Fail fast on unmapped nodes at generation time. Do not silently substitute fleet averages.
- GPU model strings are not available from Prometheus. Node→GPU mapping is maintained out-of-band by cluster operators in the SoT TOML.

## Motivation

GPU manufacturing contributes materially to embodied carbon for GPU-heavy workloads. Currently `jobcarbon` omits GPU embodied carbon, producing systematically underestimated SCI results for any job that runs on a GPU node. The change must preserve reproducibility and traceability: it must be possible to audit how a numeric embodied value was derived from the manifest and pipeline configuration alone.

## Scope

- Add `gpu_model`, `die_area_cm2`, `vram_gb`, `gpu_count`, resolved manufacturing scalars, and optionally `pcf_gco2eq` to the per-node `defaults` block for GPU-profile nodes.
- Add IF pipeline steps to compute `gpu_embodied_gco2eq` using either the manufacturer PCF or a tiered-scalar regression on die area and VRAM, and include it in `sum-carbon`.
- Add a new `gpu_count` Prometheus query (count of distinct GPU minor numbers for the job on that node) to `registry.py` and `loader.py`.
- Generator loads the SoT TOML and injects GPU metadata; if a GPU-profile node is absent from the SoT, generation fails with a clear error.
- Split the two existing GPU pipeline templates (`full_gpu`, `host_only_gpu`) into four: one PCF variant and one estimated variant per profile.

## Out of scope (MVP)

- Automatically discovering GPU model strings from Prometheus — no such metric exists on Oscar. Node→GPU mapping is external and maintained out-of-band.
- Automated remote lookup of manufacturer PCFs. PCFs are added to the SoT config manually.
- CSV export of the TOML SoT. The source-of-truth is TOML only.

## Design Decisions

### Source-of-truth config

A single TOML file (`config/gpu_embodied.toml`) is the SoT. Entries are structured **by GPU card model**, not by node. Each `[[gpus]]` entry lists the node hostnames that carry that card. This keeps the config short: adding a homogeneous rack of 64 A100 nodes requires one entry, not 64. The generator builds a `node → gpu_entry` dict at load time by inverting the `nodes` lists.

```toml
[[gpus]]
gpu_model    = "NVIDIA A100-SXM4-40GB"
die_area_cm2 = 8.26
vram_gb      = 40.0
process_nm   = 7
mem_type     = "hbm2e"
source       = "estimated"
nodes        = ["gpu001", "gpu002", "gpu003"]

[[gpus]]
gpu_model    = "NVIDIA H100 SXM5 80GB"
die_area_cm2 = 8.14
vram_gb      = 80.0
process_nm   = 4
mem_type     = "hbm3"
pcf_gco2eq   = 135000.0
source       = "pcf"
nodes        = ["dgx001", "dgx002"]
```

Fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `gpu_model` | string | yes | Human-readable model name; appears verbatim in the manifest |
| `die_area_cm2` | float | yes (estimated) | GPU die area in cm² |
| `vram_gb` | float | yes | VRAM capacity in GB |
| `process_nm` | int | yes (estimated) | Lithography node in nm; used to resolve `process_scalar_kgco2eq_per_cm2` |
| `mem_type` | string | yes (estimated) | One of `gddr6`, `hbm2`, `hbm2e`, `hbm3`; used to resolve `mem_scalar_kgco2eq_per_gb` |
| `pcf_gco2eq` | float | no | Manufacturer PCF for the whole GPU product in gCO2eq; triggers the PCF pipeline path |
| `source` | string | yes | `"pcf"` or `"estimated"`; informational, copied into the manifest |
| `nodes` | list[string] | yes | Node hostnames that carry this GPU card |

### Scalar tables (resolved by `src/gpu_config.py`)

**Manufacturing tier → chip scalar** (from Chasing Carbon / ACT literature):

| Tier | `process_nm` | `process_scalar_kgco2eq_per_cm2` |
|---|---|---|
| Legacy | ≥ 12 nm | 10.0 |
| Modern | 7–8 nm | 18.2 |
| Bleeding edge | ≤ 5 nm | 22.8 |

**Memory type → VRAM scalar** (per GB):

| `mem_type` | `mem_scalar_kgco2eq_per_gb` |
|---|---|
| `gddr6` | 0.4 |
| `hbm2` | 0.9 |
| `hbm2e` | 0.9 |
| `hbm3` | 0.9 |

HBM2 and HBM2e are treated as equal at 0.9 kgCO2eq/GB.

### GPU count

`gpu_count` is the number of GPUs assigned to the job on a given node. It is obtained at job load time via an instant Prometheus query that counts distinct `minor_number` label values in `nvidia_gpu_power_usage_milliwatts` for the job:

```promql
count(count by (minor_number) (nvidia_gpu_power_usage_milliwatts{instance=~'{node}:.*', jobid='{jobid}'}))
```

`gpu_count` is stored on `NodeData` and injected into `defaults` (it does not vary per timestep).

### Manifest content and self-documentation

The generator injects the following fields into `defaults` for GPU-profile nodes.

**PCF path:**
```yaml
defaults:
  gpu_model: "NVIDIA H100 SXM5 80GB"
  pcf_gco2eq: 135000.0
  gpu_count: 4
  grid_carbon_intensity: 381
  cpu_total: 64
  # ... other existing fields
```

**Estimated path:**
```yaml
defaults:
  gpu_model: "NVIDIA A100-SXM4-40GB"
  die_area_cm2: 8.26
  vram_gb: 40.0
  process_scalar_kgco2eq_per_cm2: 18.2
  mem_scalar_kgco2eq_per_gb: 0.9
  gpu_count: 2
  grid_carbon_intensity: 381
  cpu_total: 64
  # ... other existing fields
```

Both the raw physical parameters and the resolved scalars appear in `defaults`, so the manifest is fully self-documenting: a reader can verify the arithmetic without access to the SoT TOML.

A short caveat is appended to the manifest `description`:
- PCF path: `"GPU embodied carbon from manufacturer PCF."`
- Estimated path: `"GPU embodied carbon estimated via die area and VRAM regression."`

### Regression model and reproducibility

The estimated-path embodied carbon per GPU is computed as:

```
chip_embodied_kgco2eq = die_area_cm2 × process_scalar_kgco2eq_per_cm2
vram_embodied_kgco2eq = vram_gb      × mem_scalar_kgco2eq_per_gb
gpu_embodied_kgco2eq  = chip_embodied_kgco2eq + vram_embodied_kgco2eq
gpu_embodied_gco2eq   = gpu_embodied_kgco2eq × 1000 × gpu_count
```

This tiered-scalar formulation (rather than a generic linear regression) is used because it directly encodes the physics: manufacturing carbon per unit silicon area scales with process node density, and DRAM embodied carbon scales linearly with capacity. The scalars are derived from the ACT paper (Patterson et al.) and Chasing Carbon (Lottick et al.).

The arithmetic is executed inside the IF pipeline using only builtin plugins (`Multiply`, `Sum`, `Coefficient`). The resolved scalar values are visible in the manifest `defaults` block. No custom IF plugins are required.

### Template split

The two existing GPU templates are replaced by four:

| Template file | Profile | Path |
|---|---|---|
| `templates/full_gpu_pcf.yaml` | `full_gpu` | PCF |
| `templates/full_gpu_estimated.yaml` | `full_gpu` | Estimated |
| `templates/host_only_gpu_pcf.yaml` | `host_only_gpu` | PCF |
| `templates/host_only_gpu_estimated.yaml` | `host_only_gpu` | Estimated |

The generator selects the template based on `(node_profile, pcf_gco2eq is not None)`. The existing `full_gpu.yaml` and `host_only_gpu.yaml` are deleted.

### Pipeline steps

**Estimated path** — steps inserted between `sci-m` and `sum-carbon`:

```yaml
gpu-chip-embodied:          # Multiply: die_area_cm2 × process_scalar_kgco2eq_per_cm2 → chip_embodied_kgco2eq
gpu-vram-embodied:          # Multiply: vram_gb × mem_scalar_kgco2eq_per_gb → vram_embodied_kgco2eq
gpu-embodied-per-gpu-kg:    # Sum: [chip_embodied_kgco2eq, vram_embodied_kgco2eq] → gpu_embodied_kgco2eq_per_gpu
gpu-embodied-node-kg:       # Multiply: gpu_embodied_kgco2eq_per_gpu × gpu_count → gpu_embodied_kgco2eq_node
gpu-embodied-node-g:        # Coefficient: gpu_embodied_kgco2eq_node × 1000 → gpu_embodied_gco2eq
```

**PCF path** — single step replacing all of the above:

```yaml
gpu-embodied-pcf:           # Multiply: pcf_gco2eq × gpu_count → gpu_embodied_gco2eq
```

**Both paths** — `sum-carbon` gains a third input in GPU templates:

```yaml
sum-carbon:
  config:
    input-parameters:
      - carbon_operational
      - carbon_embodied        # CPU/DRAM embodied from existing sci-m
      - gpu_embodied_gco2eq    # GPU embodied from new steps above
    output-parameter: carbon
```

This avoids any parameter-overwrite ambiguity in IF; `carbon_embodied` from `sci-m` is left unchanged.

### Fallback and error policy

- If `pcf_gco2eq` is present in the SoT entry for a node's GPU: use the PCF pipeline path.
- If `pcf_gco2eq` is absent: use the estimated pipeline path (die area + VRAM regression).
- If a GPU-profile node (detected via Prometheus) has no entry in the SoT: generation fails immediately with a `ValueError` naming the node and instructing the operator to add it to `config/gpu_embodied.toml`.
- There is no fleet-average fallback. Silent substitution would undermine auditability.

## Files changed

| File | Change |
|---|---|
| `config/gpu_embodied.toml` | New — SoT TOML with GPU entries and node lists |
| `config/README.md` | New — operator guide for maintaining the TOML |
| `src/gpu_config.py` | New — TOML loader, node→entry lookup, scalar resolution |
| `src/registry.py` | Add `gpu_count` PromQL query |
| `src/models.py` | Add `gpu_count: int \| None` to `NodeData` |
| `src/loader.py` | Query `gpu_count` in `_process_node` for GPU profiles |
| `src/generator.py` | Load GPU config, look up entry per node, resolve scalars, select template, inject defaults, append description caveat |
| `templates/full_gpu_pcf.yaml` | New — replaces `full_gpu.yaml` for PCF path |
| `templates/full_gpu_estimated.yaml` | New — replaces `full_gpu.yaml` for estimated path |
| `templates/host_only_gpu_pcf.yaml` | New — replaces `host_only_gpu.yaml` for PCF path |
| `templates/host_only_gpu_estimated.yaml` | New — replaces `host_only_gpu.yaml` for estimated path |
| `templates/full_gpu.yaml` | Deleted |
| `templates/host_only_gpu.yaml` | Deleted |
| `tests/test_gpu_config.py` | New — unit tests for TOML loading and scalar resolution |
| `tests/test_generator_gpu.py` | New — unit tests for manifest GPU field injection and error behaviour |

## Validation, Testing, and Acceptance Criteria

### Unit tests

- `test_gpu_config.py`:
  - TOML with valid entries loads without error and produces correct `node → gpu_entry` map.
  - `process_nm` values on tier boundaries (5, 7, 8, 12) resolve to the correct scalar.
  - Unknown `mem_type` raises `ValueError`.
  - Duplicate node hostname across two `[[gpus]]` entries raises `ValueError`.
- `test_generator_gpu.py`:
  - Given a TOML with a PCF entry, the manifest includes `pcf_gco2eq` and `gpu_count` in `defaults` and selects the `_pcf` template.
  - Given a TOML with an estimated entry, the manifest includes `die_area_cm2`, `vram_gb`, `process_scalar_kgco2eq_per_cm2`, `mem_scalar_kgco2eq_per_gb`, and `gpu_count` in `defaults` and selects the `_estimated` template.
  - A GPU-profile node absent from the TOML raises `ValueError` with the node name in the message.
  - Manifest `description` contains the appropriate caveat string for each path.

### Schema validation

Produced manifests must pass any Impact Framework validation used by downstream tooling. All audit and caveat text goes into `description` rather than arbitrary top-level keys.

### End-to-end

- A manifest generated for a PCF node produces `gpu_embodied_gco2eq = pcf_gco2eq × gpu_count` and the `description` notes manufacturer PCF.
- A manifest generated for an estimated node produces the correct numeric result of the tiered-scalar formula and the `description` notes regression estimation.

## Implementation plan

1. Add `config/gpu_embodied.toml` with placeholder entries for Oscar's known GPU hardware.
2. Add `config/README.md` documenting how to add or update entries.
3. Add `src/gpu_config.py`: TOML loader, node→entry inversion, `resolve_scalars()`.
4. Add `gpu_count` to `src/registry.py` and query it in `src/loader.py`; add field to `src/models.py`.
5. Update `src/generator.py`: load GPU config, look up node, select template, inject defaults, append description caveat; raise on missing mapping.
6. Add four new GPU templates; delete `full_gpu.yaml` and `host_only_gpu.yaml`.
7. Write `tests/test_gpu_config.py` and `tests/test_generator_gpu.py`.
8. Run full test suite; validate produced manifests against IF tooling.

## Acceptance Criteria

- `config/gpu_embodied.toml` exists and is used by the generator to populate per-node GPU metadata.
- Generated manifests for GPU nodes include `gpu_model`, `gpu_count`, and either `pcf_gco2eq` (PCF path) or `die_area_cm2`, `vram_gb`, `process_scalar_kgco2eq_per_cm2`, `mem_scalar_kgco2eq_per_gb` (estimated path) in `defaults`.
- The pipeline correctly computes `gpu_embodied_gco2eq` via the PCF or estimated steps, and `sum-carbon` includes it in `carbon`.
- Generation fails with a clear `ValueError` when a GPU-profile node is absent from the SoT.
- All unit tests pass. Full test suite passes.

## Open questions / future work

- Confirm Oscar's exact GPU model strings (as they appear on nodes) and populate `config/gpu_embodied.toml` with real entries.
- Investigate providing a CLI subcommand for validating or updating the TOML SoT and checking for unmapped nodes against a live Prometheus instance.
- Consider whether `process_nm` and `mem_type` should be validated against a fixed enum at TOML load time rather than only at scalar-resolution time.

## References

- FUTURE.md §3 — GPU embodied carbon tiered approach
- METHODOLOGY.md §5 — embodied carbon and SciEmbodied plugin
- Patterson et al., "Carbon Emissions and Large Neural Network Training," 2021
- Gupta et al., "ACT: Designing Sustainable Computer Systems with an Architectural Carbon Modeling Tool," 2022
- Lottick et al., "Energy Usage Reports: Environmental awareness as part of algorithmic accountability," 2019
