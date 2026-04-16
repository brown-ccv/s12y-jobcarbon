---
title: GPU Embodied Carbon
status: proposed
owners: [@broarr]
created: 2026-04-16
---

# GPU Embodied Carbon (PRD)

Goal: close the known gap in `jobcarbon` where GPU embodied carbon is absent, producing reproducible, auditable, and schema-compatible manifests that allow per-job SCI results to include GPU manufacturing impact.

This document specifies the product requirements, data model, manifest and pipeline changes, operational behaviour, and acceptance criteria for adding GPU embodied carbon to `jobcarbon`.

Summary
- Add per-GPU embodied carbon into Impact Framework manifests used by `jobcarbon`.
- Prefer that manifests remain self-documenting: include raw GPU parameters (die area and VRAM) in the manifest and run the regression inside the pipeline so the methodology is encoded in the pipeline step(s).
- Use an internal, versioned source-of-truth config for node→GPU mapping and PCF overrides; recommend TOML for flexibility, but allow CSV export for compatibility if needed.
- Fail fast on unmapped GPU models (generation-time error). Do not silently substitute fleet averages.

Motivation
- GPU manufacturing contributes materially to embodied carbon for many workloads. Currently `jobcarbon` omits GPU embodied carbon, producing systematically underestimated results for GPU-heavy jobs.
- The change must preserve reproducibility and traceability: it should be possible to audit how a numeric embodied value was derived from the manifest and pipeline configuration.

Scope
- Include per-node GPU model metadata in the manifest: `gpu_model`, `die_area_mm2`, `vram_mib`, and optionally `pcf_gco2eq` when an authoritative manufacturer PCF is available.
- Add a pipeline step (plugin) to compute per-GPU embodied carbon using either the supplied PCF or a regression on die area and VRAM.
- Generator must look up node→GPU mapping from an internal config and inject GPU metadata into the manifest.
- On generation, if a node is known to have GPUs but the GPU model or mapping is missing, treat that as an error and abort with a clear message.

Out of scope (MVP)
- Automatically discovering GPU model strings from Prometheus (no metric available). Node→GPU mapping is external and maintained out-of-band.
- Automated remote lookup of manufacturer PCFs. PCFs may be added to the SoT config manually.

Design Decisions

- Single source-of-truth config
  - Recommendation: use a single TOML file in the repository (e g `config/gpu_embodied.toml`) as the internal source-of-truth (SoT). TOML is easy to extend (tables for GPUs, API credentials, node weights, plugin parameters) and human-editable.
  - For compatibility (if/when needed), tooling may export a CSV derived from TOML for any consumer that requires CSV. The source-of-truth remains TOML.

- Manifest content and self-documentation
  - The manifest must include, per GPU node (inside the node `defaults` block or as pipeline `inputs` where appropriate):
    - `gpu_model`: string (the model identifier as recorded in the SoT)
    - `die_area_mm2`: number (nullable; mm^2)
    - `vram_mib`: integer (MiB)
    - `pcf_gco2eq`: number (optional; total embodied grams CO2eq for the whole GPU product if manufacturer PCF is available)
  - Include global metadata caveats in the manifest `description` and/or `tags` noting when `host_only` heuristics or GPU regression estimation were used. This keeps the manifest schema-compatible while surfacing limitations to readers.

- Regression location and reproducibility
  - The regression should be executed as part of the Impact Framework pipeline (a new plugin step, e g `sci-m-gpu`) so that the manifest contains raw parameters and the pipeline contains the method. This ensures methodology remains visible and changeable in one place.
  - To keep the pipeline reproducible, regression coefficients and algorithmic choices must be declared in the plugin configuration that becomes part of the manifest (i e the plugin parameters inside `initialize.plugins` or `pipeline.compute` templates). That way a manifest + pipeline fully documents the data and model used to compute embodied GPU carbon.

- Fallback and error policy
  - If `pcf_gco2eq` exists for a GPU model, the pipeline uses it and skips the regression.
  - If `pcf_gco2eq` is absent, the pipeline runs the regression using `die_area_mm2` and `vram_mib` to estimate embodied carbon.
  - If the generator cannot find any mapping information for a node that reports GPUs (i e the node is known to have GPUs but no `gpu_model` mapping exists in the SoT), manifest generation fails with a clear error instructing the operator to update the SoT.

Data Model / Config

- Source-of-truth (example TOML layout)

  ```toml
  [gpus]
  [[gpus.entries]]
  gpu_model = "NVIDIA A100-SXM4-40GB"
  vram_mib = 40960
  die_area_mm2 = 826.0
  pcf_gco2eq = 135000.0  # optional: manufacturer PCF for the full GPU product
  source = "pcf" # or "estimated" or an attribution string

  [[gpus.entries]]
  gpu_model = "NVIDIA H100"
  vram_mib = 51200
  die_area_mm2 = 814.0
  source = "estimated"
  ```

- Manifest schema notes (per-node defaults)
  - The generator will inject these fields into `defaults` for nodes that have GPUs. Example snippet (conceptual):

  ```yaml
  defaults:
    gpu_model: "NVIDIA A100-SXM4-40GB"
    die_area_mm2: 826.0
    vram_mib: 40960
    pcf_gco2eq: 135000.0  # optional
  ```

Pipeline changes

- New pipeline plugin: `sci-m-gpu` (name tentative)
  - Purpose: compute `gpu_embodied_gco2eq` per node, then add it to `carbon_embodied` before `sum-carbon`.
  - Inputs:
    - `pcf_gco2eq` (optional) — if present, plugin uses this value per GPU and skips regression
    - `die_area_mm2` (nullable)
    - `vram_mib` (nullable)
    - `regression_coeffs` (declared in the plugin parameters embedded in the manifest/template) — the coefficients and intercept used by the regression model
    - `gpu_count` (how many GPUs assigned to the job on that node; comes from existing job metadata)
  - Behaviour:
    - If `pcf_gco2eq` is provided, compute node embodied = `pcf_gco2eq × gpu_count` (or use per-GPU semantics depending on PCF definition) and set `source = pcf`.
    - Else, if `die_area_mm2` and `vram_mib` are present, run regression:

      `embodied_per_gpu = intercept + a * die_area_mm2 + b * vram_mib`

      store `embodied_per_gpu` and multiply by `gpu_count` as appropriate. Set `source = estimated`.
    - Else, if required inputs are missing, fail the pipeline (this should not happen if generator enforces presence).

Notes on regression coefficients
- Regression coefficients should be derived from academic literature or retrofitted to known PCFs and checked into the repository as part of pipeline plugin defaults. Coefficients are visible in the manifest/plugin parameters to support auditability.

Validation, Testing, and Acceptance Criteria

- Unit tests
  - Generator unit tests that verify: given a TOML mapping, manifests include the expected `gpu_model`, `die_area_mm2`, `vram_mib`, and optional `pcf_gco2eq` fields for each GPU node.
  - Pipeline unit tests for `sci-m-gpu` plugin: uses PCF when present; uses regression when PCF absent; reproduces expected numeric outputs for known inputs.

- Schema validation
  - Produced manifests must pass any Impact Framework validation used by downstream tooling. To avoid schema incompatibility, all audit/caveat text goes into `description` and `tags` instead of arbitrary new top-level keys.

- End-to-end
  - Example job manifest generated for a DGX node with a `pcf_gco2eq` present produces a non-zero `carbon_embodied` that equals (pcf × GPU count) and the manifest `description` contains a short note "GPU embodied carbon from manufacturer PCF".
  - Example job manifest for a node with no `pcf_gco2eq` but with die and vram runs the regression in-pipeline and produces a numeric embodied value; `description` notes "GPU embodied carbon estimated via die area + VRAM regression".

Operational considerations

- Node→GPU mapping
  - Since Prometheus does not publish a GPU model string per-node, cluster operators must maintain a SoT mapping from node hostname/model → GPU entry in `config/gpu_embodied.toml`.
  - Document update process and responsibility (short README in `config/` explaining how to add or override entries).

- Visibility
  - Always include a short caveat in the manifest `description` when the job uses `host_only` heuristics or GPU regression estimation. This keeps the manifest schema-clean while surfacing limitations.

Implementation plan (recommended order)

1. Add `config/gpu_embodied.toml` as the SoT file and commit initial known entries (DGX H100 example if available, others estimated or left blank).
2. Update `loader.py` to record `gpu_model` on NodeData (if not already present) and ensure generator picks up the value from the SoT and injects `die_area_mm2`, `vram_mib`, `pcf_gco2eq` into node `defaults`.
3. Add a new pipeline plugin template step `sci-m-gpu` to the templates for `full_gpu` and `host_only_gpu` profiles. Embed regression coefficients in plugin params so the manifest includes the method.
4. Add generator logic: for nodes with GPUs, error on missing mapping; for nodes with mapping, inject the fields; ensure `description`/`tags` include caveat text when heuristics are used.
5. Write unit tests for generator and plugin behaviour.
6. Validate produced manifests against IF tooling used in CI, iterate until schema-compliant.

Acceptance Criteria

- The repository contains `docs/enhancements/01-gpu-embodied.md` (this document).
- `config/gpu_embodied.toml` exists and is used by the generator to populate per-node GPU metadata.
- Generated manifests include `die_area_mm2` and `vram_mib` for GPU nodes and pass Impact Framework validation.
- The pipeline computes GPU embodied carbon either from PCF (when provided) or by regression (when PCF absent). Numeric results match unit tests.
- Generation fails with a clear error when a GPU node lacks a mapping in the SoT.

Open questions / future work
- Decide a canonical name for the new plugin (e g `sci-m-gpu`) and finalise regression formula and coefficients after surveying literature and available PCFs.
- Investigate providing a maintenance UI or CLI for updating the TOML SoT and exporting CSV for compatibility with external tools.
- Consider a small manifest-side 'metadata' field accepted by IF (or sanctioned by IF maintainers) for richer notes, which would allow human-readable comments without breaking schema validation.

References
- FUTURE.md §3 — GPU embodied carbon tiered approach
- METHODOLOGY.md §5 — embodied carbon and SciEmbodied plugin
