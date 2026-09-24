# mpi-duplicate-detection

Duplicate patient-record detection for a Master Patient Index (MPI), with an MCP server. **Synthetic data only. Not for production use. Not for real patient data.**

Author: Christopher Scharenberg. Write-up with results and limits: [`docs/case_study.md`](docs/case_study.md). Field definitions: [`DATA_DICTIONARY.md`](DATA_DICTIONARY.md).

## What's here

| File | Purpose |
|---|---|
| `patient_match.py` | Scores a pair of records (0 to 1) with a verdict and reasons |
| `nickname_lookup.py`, `names.csv` | Nickname and name-variant matching |
| `check_nickname_overmerge.py` | Reproduces the union-find over-merging bug found during evaluation |
| `make_synthetic_eval.py` | Injects labeled duplicates and look-alikes into Synthea data, scores every pair, reports precision and recall |
| `mpi_dedup_server.py` | FastMCP server exposing `check_duplicate` |
| `nemotron_agent_test.py` | Local model calls `check_duplicate` through Ollama tool-calling |
| `test_mcp_server.py` | Calls `check_duplicate` through the FastMCP server object and compares with a direct call |
| `unmatched_results_report.py` | Read-only report of OpenEMR lab results that created placeholder patients |
| `results/` | Raw output of the runs reported in the case study |

## Setup

```
pip install -r requirements.txt
python3 patient_match.py        # smoke test with invented records
```

## Generate synthetic data

Patients come from [Synthea](https://github.com/synthetichealth/synthea). Reported runs used commit `d9d07a6eef91ee5144293b42ab64224d84d124f8`:

```
./run_synthea -s 42 -cs 42 -p 500 -r 20260923 --exporter.fhir.export=true Massachusetts
```

This wrote 586 patient files (500 living, 86 deceased); the eval uses all of them. Put the FHIR output in `fhir/`. I have not verified that Synthea reproduces byte-identical files from the same seed, so regenerating gives a comparable set, not necessarily an identical one.

## Run the evaluation

```
python3 make_synthetic_eval.py --fhir-dir fhir --seed 42 --n-each 25 --out eval_seed42
python3 make_synthetic_eval.py --fhir-dir fhir --seed 7 --n-each 25 --out eval_seed7
```

Same seed and same `fhir/` folder reproduce the same records. Outputs are printed and saved as CSVs (ignored by git).

## Run the agent test

Needs [Ollama](https://ollama.com) running with a tool-calling model. Check the tag with `ollama list`.

```
OLLAMA_MODEL=nemotron-3.5-lightning python3 nemotron_agent_test.py
```

Model output varies between runs.

## Run the MCP server

Needs Python 3.10 or newer (FastMCP requirement).

```
python3 mpi_dedup_server.py
python3 test_mcp_server.py      # in-process check that the MCP tool matches a direct call
```

See the FastMCP documentation for connecting a client.

## Unmatched-results report

```
python3 unmatched_results_report.py --demo        # sample data, no database
```

Against a sandbox database, copy `.env.example` to `.env`, fill it in, and run without `--demo`. The script only reads.

## Known limits

Synthetic data only, hand-set thresholds, a small 56-patient base set, and injected errors chosen by the author. Full list in the case study.

## Third-party sources and licenses

- **Nicknames:** `names.csv` comes from the [carltonnorthern/nicknames](https://github.com/carltonnorthern/nicknames) project. License text: `LICENSE-nicknames-carltonnorthern.txt` (Apache-2.0). Unmodified: its content matched the upstream `names.csv` when compared in September 2026 (line endings aside). See the case study for the maintainers' caution about coverage bias.
- **Synthea:** Apache-2.0 (from the `LICENSE` file of commit `d9d07a6eef91ee5144293b42ab64224d84d124f8`). Synthea output is generated locally and not committed.
- **OpenEMR:** GNU GPL v3 (per its `LICENSE` file), version 8.2.0 (per `version.php`), local checkout at commit `6125a2fd8089c8bcc3848071c1293c60e27a7585`. No OpenEMR code is included; the unmatched-results report only queries its database.
- **Libraries** (licenses from installed package metadata): FastMCP Apache-2.0; jellyfish MIT (license classifier); ollama MIT; PyMySQL MIT.
- **Model:** `nemotron-3.5-lightning:latest` (32.9B parameters, Q4_K_M quantization), run locally through Ollama and licensed by NVIDIA under the NVIDIA Open Model License Agreement (last modified October 24, 2025). Model weights are not included in this repo.

## License

MIT. See [`LICENSE`](LICENSE). The nickname data keeps its own license, noted above.
