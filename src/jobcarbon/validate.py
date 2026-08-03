from .config import Config, is_pcf_spec


def find_problems(config: Config) -> list[str]:
    """Return offline structural problems with a loaded config; empty if valid.

    Checks only what can be verified without a cluster (no Prometheus): that
    every hardware entry is well-formed and that every GPU node also has a CPU
    entry, since GPU nodes need a CPU die area for `--embodied`.
    """
    problems: list[str] = []

    # Every GPU node is also a CPU node; without a [[cpus]] entry it would fail
    # at manifest generation under --embodied.
    for node in sorted(config.node_map):
        if node not in config.cpu_node_map:
            problems.append(
                f"node '{node}' has a [[gpus]] entry but no [[cpus]] entry "
                f"(GPU nodes need a CPU die area for --embodied)"
            )

    seen_cpu: set[str] = set()
    for spec in config.cpu_node_map.values():
        model = spec["cpu_model"]
        if model in seen_cpu:
            continue
        seen_cpu.add(model)
        if "die_area_sq_cm" not in spec:
            problems.append(f"[[cpus]] '{model}' is missing die_area_sq_cm")

    seen_gpu: set[str] = set()
    for spec in config.node_map.values():
        model = spec["gpu_model"]
        if model in seen_gpu:
            continue
        seen_gpu.add(model)
        if is_pcf_spec(spec):
            continue
        for key in ("die_area_sq_cm", "vram_gb"):
            if key not in spec:
                problems.append(f"[[gpus]] '{model}' is missing {key}")
        if spec.get("process") not in config.process_scalars:
            problems.append(
                f"[[gpus]] '{model}' has unknown process {spec.get('process')!r}"
            )
        if spec.get("mem_type") not in config.mem_scalars:
            problems.append(
                f"[[gpus]] '{model}' has unknown mem_type {spec.get('mem_type')!r}"
            )

    return problems
