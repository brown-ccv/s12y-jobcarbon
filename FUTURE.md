# Future Work

Known limitations and planned improvements to `jobcarbon`

## 1. SCI functional unit (R) normalization

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

## 2. PUE (Power Usage Effectiveness)

### Problem

The pipeline measures server-level power draw but not data center facility overhead — cooling, UPS losses, power distribution, and lighting A PUE > 1.0 means the grid draw is higher than the server-level measurement by a factor of PUE; operational carbon is therefore understated by the same factor

### Approach

Multiply `node_power_kw × PUE` before `calculate-energy` in all templates PUE would be a scalar in `config/jobcarbon.toml` and injected into node `defaults`

**Blocked:** Oscar's data center PUE is not currently available from facilities Once it is, this is a small change to `config/jobcarbon.toml` and the templates

---

## 3. Storage and network scope

### Problem

Storage I/O and network transfer have both operational energy costs (drives and switches spinning) and embodied carbon (manufacturing) Both are currently out of scope (see `METHODOLOGY.md §1`)

### Approach

Per-job storage and network I/O metrics would need to be available from Prometheus Li et al. (HotCarbon 2024) models these as fleet-wide constants rather than per-job values; we do not want to add constants that are not attributable to the specific job

**Blocked:** no per-job storage or network telemetry is currently available in the Oscar Prometheus instance
