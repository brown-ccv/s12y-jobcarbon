# Future Work

Known limitations and planned improvements to `jobcarbon`

## 1. Dynamic grid carbon intensity via Electricity Maps

### Problem

The current grid carbon intensity is a static annual average (381 gCO2eq/kWh, EPA eGRID 2022 NEWE subregion — see `METHODOLOGY.md §3`) This erases real temporal variation: grid carbon intensity in New England varies by a factor of 2–3x across hours and seasons depending on renewable generation and demand Two identical jobs run at different times of day will receive the same carbon score under the current model, which undermines the tool's value for time-of-submission scheduling decisions

### Why direct intensity, not lifecycle

Electricity Maps provides two emission factor types: `lifecycle` (LCA, includes upstream emissions from fuel extraction and plant construction) and `direct` (operational combustion emissions only) For SCI operational carbon — which measures the emissions caused by the job's electricity draw — `direct` is the more appropriate signal: it reflects what the grid actually emits in response to the job's load Lifecycle factors include fixed upstream costs that are not attributable to any individual consumer's demand

Electricity Maps does not provide a true marginal operating emissions rate (MOER) — it provides average intensity across all generation sources consumed in the zone The `direct` average is the closest available proxy for marginal operational emissions on this platform

### Implementation plan

- **Data source:** Electricity Maps `GET /v4/carbon-intensity/past-range` with `zone=US-NE-ISNE`, `emissionFactorType=direct`, `temporalGranularity=hourly` The cluster location is fixed; no node-to-region mapping is needed
- **Time alignment:** query over the job's Prometheus time window (`start`, `end` in ISO format) The API returns one value per hour; each 60-second Prometheus timestep is assigned the intensity of the hour it falls within (step-function assignment, not interpolation) Jobs longer than 10 days require looping over 10-day chunks (the API's range limit at hourly granularity)
- **Schema change:** `grid_carbon_intensity` currently lives in the per-node `defaults` block (a single scalar) With per-hour values it becomes a per-timestep field and must move into each row of the `inputs` list in `generator.py:_build_node` The `sci-o` template plugin is unchanged — it already reads `grid_carbon_intensity` as an input field from each row
- **New module:** `src/gridintensity.py` handles the API call and returns a list of `(datetime, gco2eq_per_kwh)` tuples covering the job window, ready for alignment against Prometheus timesteps in `generator.py`
- **Auth:** API key read from the `JOBCARBON_ELECTRICITY_MAPS_KEY` environment variable
- **Fallback:** if the key is absent, the API is unreachable, or the job window has no coverage in the API response, emit a warning to stderr and fall back to the hardcoded `_DEFAULT_GRID_CARBON_INTENSITY` (381 gCO2eq/kWh) as a scalar in `defaults` — preserving current behavior

---

## 2. Empirical CPU/DRAM weight derivation for `host_only` pipelines

### Problem

The `host_only` and `host_only_gpu` profiles attribute a fraction of whole-host power to a job using a weighted sum of CPU and memory reservation shares with weights 0.7 and 0.3 respectively These weights are a placeholder (see `METHODOLOGY.md §4`) and have not been validated against measured data on Oscar's hardware Using the wrong weights produces a systematically biased `node_power_kw` for every `host_only` job

### Approach

**Offline characterisation** Some nodes in Oscar have both `host_power` (from Scaphandre's whole-host metric) and component-level `cpu_power` and `dram_power` data On those nodes, the empirical weights can be derived directly:

```
w_cpu  = mean(cpu_power)  / mean(host_power)
w_dram = mean(dram_power) / mean(host_power)
```

computed over a representative historical window (e.g. 30 days of production workloads) using `query_range` against the existing Prometheus instance The result is a per-node (or per-node-model) pair of weights that replace the 0.7/0.3 placeholder

Derived weights should be stored in `config/jobcarbon.toml` keyed by node hostname or hardware model string `loader.py` looks up the weights for each node at job time and injects them into the node's `defaults` block; the pipeline templates remain unchanged If a node has no entry, fall back to 0.7/0.3 with a warning to stderr

### Limitation to disclose

Weights derived from historical load distribution reflect the average workload mix over the characterisation window They may not be representative for atypical workloads (e.g. jobs that saturate memory bandwidth while leaving CPUs mostly idle) This limitation should be disclosed when `host_only` results are reported

---

## 3. SCI functional unit (R) normalization

### Problem

The current pipeline reports total carbon per job run (`R = 1`) A 10-minute job and a 10-hour job doing the same work are not directly comparable on this scale Without a normalized functional unit, the SCI score conflates job carbon efficiency with job duration

### Approach

Add a `--functional-unit` CLI option to both `main` and `batch` commands with four values:

| Value | R | Output unit |
|---|---|---|
| `job` (default) | 1 job run | gCO2eq |
| `seconds` | wall-clock duration in seconds | gCO2eq/s |
| `minutes` | wall-clock duration in minutes | gCO2eq/min |
| `hours` | wall-clock duration in hours | gCO2eq/hr |

Wall-clock duration is computed as `n_timesteps × 60` seconds (already known from the Prometheus time window) and scaled to the chosen unit

**Pipeline change:** add a final `normalize` step to all 6 templates — a `Divide` plugin that computes `carbon / functional_unit → sci` For `--functional-unit job`, inject `functional_unit = 1` into node `defaults` making the divide a no-op For time-based units, inject the computed duration in the appropriate unit `sci` is added to `aggregation.metrics` alongside `carbon`

**Implementation touches:**
- `jobcarbon.py` — add `--functional-unit` option to `main` and `batch` commands
- `generator.py` — `_build_node` computes and injects `functional_unit` into `defaults`
- All 6 templates — new `normalize` Divide step; `sci` added to `aggregation.metrics`

---

## 4. PUE (Power Usage Effectiveness)

### Problem

The pipeline measures server-level power draw but not data center facility overhead — cooling, UPS losses, power distribution, and lighting A PUE > 1.0 means the grid draw is higher than the server-level measurement by a factor of PUE; operational carbon is therefore understated by the same factor

### Approach

Multiply `node_power_kw × PUE` before `calculate-energy` in all templates PUE would be a scalar in `config/jobcarbon.toml` and injected into node `defaults`

**Blocked:** Oscar's data center PUE is not currently available from facilities Once it is, this is a small change to `config/jobcarbon.toml` and the templates

---

## 5. Storage and network scope

### Problem

Storage I/O and network transfer have both operational energy costs (drives and switches spinning) and embodied carbon (manufacturing) Both are currently out of scope (see `METHODOLOGY.md §1`)

### Approach

Per-job storage and network I/O metrics would need to be available from Prometheus Li et al. (HotCarbon 2024) models these as fleet-wide constants rather than per-job values; we do not want to add constants that are not attributable to the specific job

**Blocked:** no per-job storage or network telemetry is currently available in the Oscar Prometheus instance
