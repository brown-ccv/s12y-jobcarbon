# Die-Area Reference

Die area (cm², per socket for CPUs / per die for GPUs) is the main input embodied carbon needs
beyond what Prometheus already provides (see METHODOLOGY.md §5). It is a **hardware fact, not
site configuration**, so this page is a shared reference: look up your hardware here, then map it
to nodes in your config. If a part is missing, derive it with the method below and add a row —
it then helps every other site.

Sources are third-party die measurements; neither Intel, AMD, nor NVIDIA publishes official die
areas. Figures marked † are approximate, pending independent confirmation.

---

# CPUs

Only the node lists are site-specific. CPU model is not in Slurm GRES and cannot be
auto-discovered, so `[[cpus]]` node lists are filled in by hand (get models per node with
`pdsh … lscpu | grep 'model name'` or equivalent).

## Catalog

| CPU model (`lscpu` "model name") | µarch | die (cm²/socket) |
|---|---|---|
| Intel Xeon Gold 6126 | Skylake-SP (HCC) | 4.85 |
| Intel Xeon Gold 6142 | Skylake-SP (HCC) | 4.85 |
| Intel Xeon Gold 6242 | Cascade Lake-SP (HCC) | 4.84 |
| Intel Xeon Platinum 8268 | Cascade Lake-SP (XCC) | 6.94 |
| Intel Xeon E5-2698 v4 | Broadwell-EP (HCC) | 4.56 |
| Intel Xeon Platinum 8358 | Ice Lake-SP (XCC) | 6.60 † |
| Intel Xeon Platinum 8480C | Sapphire Rapids (4-tile XCC) | 16.00 † |
| AMD EPYC 7532 | Rome / Zen 2 (8×CCD) | 10.08 |
| AMD EPYC 7662 | Rome / Zen 2 (8×CCD) | 10.08 |
| AMD EPYC 7413 | Milan / Zen 3 (4×CCD) | 7.36 |
| AMD EPYC 7763 | Milan / Zen 3 (8×CCD) | 10.56 |
| AMD EPYC 9354 | Genoa / Zen 4 (8×CCD) | 9.25 |
| AMD EPYC 9554 | Genoa / Zen 4 (8×CCD) | 9.25 |
| AMD EPYC 9684X | Genoa-X / Zen 4 (12×CCD + V-Cache) | 16.21 |

## Deriving a missing CPU

Die area is **total silicon per socket** — the flat BoaviztAPI scalar has no notion of process
node, so sum all dies in the package.

**Intel (monolithic):** pick the die class by core count and use its area. Both figures below
are the physical die (all cores, whether or not the SKU fuses some off).

| Die class | Skylake / Cascade Lake-SP | Broadwell-EP | Ice Lake-SP | Sapphire Rapids |
|---|---|---|---|---|
| LCC | 3.22 | 2.46 | — | — |
| MCC | — | 3.06 | ~5.7 | ~7.5 (monolithic) |
| HCC | 4.85 | 4.56 | — | — |
| XCC | 6.94 | — | 6.60 | 16.00 (4 × ~4.0 tiles) |

**AMD (chiplet):** `die = N_CCD × CCD_area + IOD_area` (+ `N_CCD × V-Cache_area` for X-series).
Derive `N_CCD` from the SKU's L3 cache at **32 MB per CCD** (e.g. 256 MB L3 → 8 CCDs).

| Generation | CCD area (cm²) | IOD area (cm²) | V-Cache (cm²) |
|---|---|---|---|
| Zen 2 (Rome) | 0.74 | 4.16 | — |
| Zen 3 (Milan) | 0.80 | 4.16 | 0.41 |
| Zen 4 (Genoa) | 0.66 | 3.97 | 0.36 |

Worked example — EPYC 9684X (96c, 1152 MB L3 → 12 CCDs, Genoa-X):
`12 × 0.66 + 3.97 + 12 × 0.36 = 7.92 + 3.97 + 4.32 = 16.21 cm²`.

## CPU sources

- Intel die classes — WikiChip (Skylake-SP, Cascade Lake-SP, Ice Lake-SP), WikiChip Xeon
  E5-2698 v4, WCCFTech (Sapphire Rapids XCC tile ~400 mm²)
- AMD CCD / IOD — WikiChip Zen 2, TechPowerUp Zen 4 die analysis
- AMD 3D V-Cache — TechInsights, Tom's Hardware (Genoa-X)

---

# GPUs

Unlike CPUs, GPU node lists are auto-populated from Slurm GRES by `create-config`, and these die
areas are also seeded in `SEED_SPECS` (`config.py`) for that generation. This table is the human
reference with citations. GPU embodied carbon per die is `die_area_sq_cm × process_scalar` (+ a
VRAM term); see METHODOLOGY.md §5.

## Catalog (die-area / estimated path)

| GPU model | die (chip) | process | die (cm²) | source |
|---|---|---|---|---|
| NVIDIA Quadro RTX 6000 | TU102 | tsmc-12n | 7.54 | [TechPowerUp][tpu-tu102] |
| NVIDIA GeForce RTX 3090 | GA102 | samsung-8n | 6.28 | [TechPowerUp][tpu-ga102] |
| NVIDIA RTX A5500 | GA102 | samsung-8n | 6.28 | [TechPowerUp][tpu-ga102] |
| NVIDIA RTX A5000 | GA102 | samsung-8n | 6.28 | [TechPowerUp][tpu-ga102] |
| NVIDIA A40 | GA102 | samsung-8n | 6.28 | [TechPowerUp][tpu-ga102] |
| NVIDIA RTX A6000 | GA102 | samsung-8n | 6.28 | [TechPowerUp][tpu-ga102] |
| NVIDIA A2 | GA107 | samsung-8n | 2.00 | [TechPowerUp][tpu-ga107] |
| NVIDIA L40 | AD102 | tsmc-n4 | 6.09 | [TechPowerUp][tpu-ad102] |
| NVIDIA L40S | AD102 | tsmc-n4 | 6.09 | [TechPowerUp][tpu-ad102] |
| NVIDIA H100 NVL | GH100 | tsmc-n4 | 8.14 | [Chips and Cheese][cnc-h100] |
| NVIDIA GH200 480GB | GH100 | tsmc-n4 | 8.14 | [Chips and Cheese][cnc-h100] |
| NVIDIA RTX Pro 6000 Blackwell Max-Q | GB202 | tsmc-n4p | 7.50 † | [Chips and Cheese][cnc-blackwell] |

## Directly-sourced figure (no die area)

These use a published cradle-to-gate figure instead of the die-area estimate — a manufacturer
PCF (stored as `pcf_carbon_per_gpu`) or a third-party LCA (stored as `lca_carbon_per_gpu`). The
manufacturer PCFs are baseboard figures (8 GPUs) divided by 8; the A100 LCA is already per-GPU.

| GPU model | per-GPU gCO2eq | config key | source |
|---|---|---|---|
| NVIDIA A100 SXM4 80GB | 127,600 | `lca_carbon_per_gpu` | [A100 LCA (arXiv 2509.00093)][a100-lca] |
| NVIDIA H100 SXM5 80GB | 164,000 (1,312,000 / 8) | `pcf_carbon_per_gpu` | [NVIDIA HGX-H100 PCF][h100-pcf] |
| NVIDIA B200 | 284,250 (2,274,000 / 8) | `pcf_carbon_per_gpu` | [NVIDIA HGX-B200 PCF][b200-pcf] |

---

## Caveats

- The flat CPU `cpu_die_scalar` (1970 gCO2eq/cm²) assumes a leading-edge logic node. AMD I/O
  dies (GF 14 nm / TSMC N6) are far cheaper per cm² than that, so summing them overcounts
  I/O-die carbon. Accepted as part of the flat-model simplification.
- Within-family CPU die area varies by die class (Intel) or CCD count (AMD) — up to ~2×. Do not
  approximate by CPU family; use the specific model.
- GB202 (750 mm²) is from a die-level analysis, not a vendor figure; update if NVIDIA or a
  third-party LCA publishes a revised number.

[tpu-tu102]: https://www.techpowerup.com/gpu-specs/nvidia-tu102.g813
[tpu-ga102]: https://www.techpowerup.com/gpu-specs/nvidia-ga102.g930
[tpu-ga107]: https://www.techpowerup.com/gpu-specs/nvidia-ga107.g988
[tpu-ad102]: https://www.techpowerup.com/gpu-specs/nvidia-ad102.g1005
[cnc-h100]: https://chipsandcheese.com/p/nvidias-h100-funny-l2-and-tons-of-bandwidth
[cnc-blackwell]: https://chipsandcheese.com/p/blackwell-nvidias-massive-gpu
[a100-lca]: https://arxiv.org/abs/2509.00093
[h100-pcf]: https://images.nvidia.com/aem-dam/Solutions/documents/HGX-H100-PCF-Summary.pdf
[b200-pcf]: https://images.nvidia.com/aem-dam/Solutions/documents/HGX-B200-PCF-Summary.pdf
