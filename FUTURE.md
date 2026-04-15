# Future Work

Known limitations and planned improvements to `jobcarbon`

## 1. Dynamic grid carbon intensity via WattTime MOER

### Problem

The current grid carbon intensity is a static annual average (381 gCO2eq/kWh, EPA eGRID 2022 NEWE subregion — see `METHODOLOGY.md §3`). This erases real temporal variation: grid carbon intensity in New England varies by a factor of 2–3x across hours and seasons depending on renewable generation and demand. Two identical jobs run at different times of day will receive the same carbon score under the current model, which undermines the tool's value for time-of-submission scheduling decisions.

### Why MOER, not average intensity

The marginal operating emissions rate (MOER) reflects the carbon intensity of the marginal generator dispatched in response to incremental load — i.e. the generator that the job's power draw actually caused to be dispatched. Average grid intensity includes baseload generation that runs regardless of the job and is therefore not attributable to it. For a tool intended to inform scheduling decisions about *additional* jobs, MOER is the more defensible quantity. Electricity Maps offers a similar API and is a viable alternative; MOER from WattTime is preferred.

### Implementation plan

- **Data source:** WattTime `v3/historical` endpoint, authenticated with a contract credential stored in environment config. BA: `ISONE_RIMA` (Rhode Island within ISO-NE). The cluster location is fixed; no node-to-region mapping is needed.
- **Time alignment:** query MOER over the job's time window at the same 60-second resolution as Prometheus. Align MOER timestamps to Prometheus timestamps using the same inner-join pattern already used in `synthesis.py`. Nearest-neighbour or linear interpolation will be needed since WattTime's finest resolution is 5 minutes.
- **Schema change:** `grid_carbon_intensity` currently lives in the per-node `defaults` block in the manifest (a single scalar). With per-interval MOER it becomes a per-timestep value and must move into each row of the `inputs` list. This requires a change to `generator.py:_build_node`. The `sci-o` template plugin is unchanged — it already reads `grid_carbon_intensity` as an input field from each row.
- **Fallback:** if the WattTime API is unavailable or credentials are absent, fall back to the hardcoded 381 gCO2eq/kWh and emit a warning to stderr.

---

## 2. Empirical CPU/DRAM weight derivation for `host_only` pipelines

### Problem

The `host_only` and `host_only_gpu` profiles attribute a fraction of whole-host power to a job using a weighted sum of CPU and memory reservation shares with weights 0.7 and 0.3 respectively. These weights are a placeholder (see `METHODOLOGY.md §4`) and have not been validated against measured data on Oscar's hardware. Using the wrong weights produces a systematically biased `node_power_watts` for every `host_only` job.

### Approach

**Offline characterisation.** Some nodes in Oscar have both `host_power` (from Scaphandre's whole-host metric) and component-level `cpu_power` and `dram_power` data. On those nodes, the empirical weights can be derived directly:

```
w_cpu  = mean(cpu_power)  / mean(host_power)
w_dram = mean(dram_power) / mean(host_power)
```

computed over a representative historical window (e.g. 30 days of production workloads) using `query_range` against the existing Prometheus instance. The result is a per-node (or per-node-model) pair of weights that replace the 0.7/0.3 placeholder.

Derived weights should be stored in a config file (e.g. `config/node_weights.yaml`) keyed by node hostname or hardware model string. `loader.py` looks up the weights for each node at job time and injects them into the node's `defaults` block; the pipeline templates remain unchanged. If a node is absent from the config, fall back to 0.7/0.3 with a warning.

### Limitation to disclose

Weights derived from historical load distribution reflect the average workload mix over the characterisation window. They may not be representative for atypical workloads (e.g. jobs that saturate memory bandwidth while leaving CPUs mostly idle). This limitation should be disclosed when `host_only` results are reported.

---

## 3. GPU embodied carbon

### Problem

GPU embodied carbon is currently zero for all node profiles. For jobs running on GPU nodes (`full_gpu`, `host_only_gpu`) this is a known and potentially significant underestimate: GPU hardware is materials- and energy-intensive to manufacture.

### Tiered approach

A single method cannot cover all hardware present on Oscar, so a tiered approach is proposed:

**Tier 1 — Manufacturer PCF (where available).** Some GPU hardware has a
manufacturer-published Product Carbon Footprint (PCF) covering the full lifecycle (manufacturing, transport, use, end-of-life). Where a PCF exists for the GPU model in use, it should be used directly. NVIDIA publishes a PCF for DGX H100 systems; this is the only currently known example on Oscar. Per-GPU embodied carbon is the system-level PCF divided by GPU count.

**Tier 2 — Regression estimate (no PCF).** For GPU models without a published PCF, an estimate can be derived from die area (mm^2) and VRAM capacity (MiB), following the approach used in prior HPC carbon estimation literature [Lottick et al., "Energy Usage Reports: Environmental awareness as part of algorithmic accountability," 2019; Patterson et al., "Carbon Emissions and Large Neural Network Training," 2021]. The relationship between silicon area, DRAM capacity, and manufacturing carbon has been characterised empirically from disclosed PCFs; the regression is applied to hardware for which no PCF exists.

Both tiers should be stored in a single lookup table, e.g. `config/gpu_embodied.csv`, with columns:

| Column | Description |
|---|---|
| `gpu_model` | Model string as reported by `nvidia_gpu_name` in Prometheus |
| `vram_mib` | VRAM capacity in MiB |
| `die_area_mm2` | GPU die area in mm² (Tier 2 only; blank for Tier 1) |
| `embodied_gco2eq` | Estimated total embodied carbon in gCO2eq |
| `source` | `pcf` or `estimated` |

**Uncertainty:** Tier 2 estimates carry substantial uncertainty — die area figures for most GPUs are not manufacturer-disclosed and must be sourced from public reverse-engineering analyses

### Integration plan

- `loader.py` queries `nvidia_gpu_name` from Prometheus at job time (instant query, same pattern as capacity queries) and stores the GPU model string on `NodeData`.
- At manifest generation time, `generator.py` looks up embodied carbon from the CSV and injects it as a per-node scalar in `defaults`.
- A new `sci-m-gpu` plugin step (a `Coefficient` or `Sum`) is added to the `full_gpu` and `host_only_gpu` pipeline templates to add GPU embodied carbon to `carbon_embodied` before the `sum-carbon` step.
- If the GPU model is not found in the CSV, fall back to the average `embodied_gco2eq` across all entries in the CSV as a fleet-average estimate, clearly flagged in output.
