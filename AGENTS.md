# AGENTS.md

Python CLI that queries Prometheus for Oscar Slurm job telemetry and emits a
complete [Impact Framework](https://if.greensoftware.foundation/) (`if-run`)
manifest YAML. It does **not** run `if-run` itself.

Package manager is **`uv`**. The package lives at `src/jobcarbon/`, installed
editable; tests import as `from jobcarbon.x import ...`. No build step, no type
checker.

## Commands

```sh
uv run pytest tests/                      # all tests (offline, `responses`-mocked)
uv run ruff check src/                     # lint
uv run ruff format src/                     # format
uv run jobcarbon manifest $JOB_ID          # generate manifest to stdout
uv run jobcarbon manifest $JOB_ID --embodied   # + embodied carbon estimate
uv run jobcarbon batch $JOB_ID ...         # multiple jobs, one .yaml each
```

## More

- [Architecture & source layout](docs/architecture.md)
- [Environment variables](docs/environment.md)
- [Testing](docs/testing.md)
