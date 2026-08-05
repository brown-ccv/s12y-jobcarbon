# Testing

Tests are fully offline — all Prometheus HTTP calls are mocked with the
`responses` library via fixtures in `tests/conftest.py`. There are no
integration tests that require a live Prometheus instance.

```sh
uv run pytest tests/                       # all tests
uv run pytest tests/test_synthesis.py -v   # a single file
```
