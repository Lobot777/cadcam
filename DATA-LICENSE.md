# Data and artifact licensing

This repository contains two kinds of material under different licences.

## Software — Apache License 2.0

All source code under `src/`, `scripts/`, `tests/`, the build scripts and the
LaTeX sources are licensed under the Apache License, Version 2.0. See `LICENSE`.

## Data, reports, figures and model weights — CC BY 4.0

The following are licensed under the Creative Commons Attribution 4.0
International licence (CC BY 4.0):

- experimental reports under `reports/` (JSON, Markdown);
- figures under `reports/figures/` and `latex/figures/`;
- generated G-code, STEP and STL artifacts under `reports/`;
- trained model weights (`reports/*.pt`);
- face-level annotations under `reports/annotation/`;
- the human-study response records under `reports/m3_human*/`.

Attribution: Chen, W. *CAD2CAM: verification-grounded agents for spatial and
manufacturing modalities*, 2026. Please cite the accompanying paper when reusing
these artifacts.

The full legal text of CC BY 4.0 is available at:
https://creativecommons.org/licenses/by/4.0/legalcode

### Third-party datasets — NOT redistributed here

The following are obtained from their upstream distributors and are **not**
included in this repository. Their own licences and attribution requirements
apply when readers download them:

- **MFCAD++** — Queen's University Belfast, CC BY.
  DOI: 10.17034/d1fec5a0-8c10-4630-b02e-b92dc81df823.
- **130-part external set** — derived from MFCAD (MIT licence); the source STEP
  files must be obtained from the upstream distributor. Selection and scaling
  scripts are provided here, not the source solids.

The ten anchor parts from the RPTU Zenodo record 17035762 are redistributed
under CC BY 4.0 with original attribution retained.

## Credentials

`.workbuddy/cloud.json` and any `.env` / `*.key` / `*.pem` files are excluded
from version control. The cloud API key required to reproduce model calls must
be supplied by the reader through their own environment.
