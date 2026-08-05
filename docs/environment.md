# Environment variables

| Variable | Default | Notes |
|---|---|---|
| `JOBCARBON_PROMETHEUS_URL` | `http://localhost:9390` | Base URL, no trailing slash, no `/api/v1` suffix |
| `JOBCARBON_STEP_SECONDS` | `60` | Scrape resolution; also hardcoded as `duration` in every `Observation` |
| `JOBCARBON_LOOKBACK_DAYS` | `30` | Range for initial job/node discovery query |
| `JOBCARBON_GRID_CARBON_INTENSITY` | `381` | gCO2eq/kWh; overrides `jobcarbon.toml` value |
| `JOBCARBON_CPU_LIFESPAN_YEARS` | — | Server amortisation period; overrides `jobcarbon.toml` value |
| `JOBCARBON_GPU_LIFESPAN_YEARS` | — | GPU amortisation period; overrides `jobcarbon.toml` value |
| `JOBCARBON_MAX_SAMPLES` | — | Max samples per Prometheus query chunk; overrides `jobcarbon.toml` value |
