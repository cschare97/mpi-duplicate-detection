# Duplicate patient-record detection: a case study

**Synthetic data only. Not for production use.** Author: Christopher Scharenberg

## 1. Problem
Clinics get duplicate charts when registration errors (typos, swapped name fields, mistyped SSNs) create a second record for the same person. Merging two different people is the worse error. This project scores record pairs, explains each score, and leaves the merge decision to a person.

## 2. Setup
- Synthea commit `d9d07a6` (Apache-2.0): `./run_synthea -s 42 -cs 42 -p 500 -r 20260923 --exporter.fhir.export=true Massachusetts`. 586 patient files (500 living, 86 deceased), all used. No real patient or employer data. Full commit hash in the README.
- Python 3.12.11, jellyfish 1.2.1, FastMCP 4.0.8, `ollama` client 0.6.2, Ollama 0.34.2, model `nemotron-3.5-lightning:latest` (32.9B parameters, Q4_K_M; NVIDIA Open Model License).
- OpenEMR 8.2.0 (GPL v3), local checkout, used only by the unmatched-results report, which was run in demo mode.

## 3. Method
- **`patient_match.py`:** weighted score: SSN 0.40, DOB 0.30, last name 0.18, first name 0.12. Jaro-Winkler for names; Levenshtein plus rules for SSNs (exact, masked, transposed, near-typo, collision); first/last swap detection. 0.75 or higher is `likely_duplicate`, 0.45 to 0.75 is review, below is `not_a_match`. Every result lists its reasons.
- **`nickname_lookup.py`:** names are variants if one is a listed nickname of the other or both share a formal name. My first version used union-find clustering and the evaluation exposed it: 1,096 of 2,252 names in one cluster, so Christopher and Elizabeth matched. `check_nickname_overmerge.py` reproduces this.
- **`make_synthetic_eval.py`:** injects 25 labeled duplicates for each of 9 error types and 25 look-alike non-duplicates for each of 4 types (twin, SSN collision, near-SSN, same DOB), then scores all pairs against ground truth.
- **`mpi_dedup_server.py`, `test_mcp_server.py`, `nemotron_agent_test.py`:** an MCP tool, an in-process check that it equals a direct call, and a local model calling it through Ollama.
- **`unmatched_results_report.py`:** one read-only `SELECT` on OpenEMR's `pnotes` for lab results that created placeholder patients. Its name regex missed "Mary Ann Smith"; fixed.

## 4. Results
911 records (586 base + 325 injected), 414,505 pairs, scan about 8 s.

| | Seed 42 | Seed 7 |
|---|---|---|
| Likely or review: precision / recall | 0.5794 / 1.0000 | 0.6394 / 0.9888 |
| TP / FP / FN | 270 / 196 / 0 | 266 / 150 / 3 |
| Likely only: precision / recall | 1.0000 / 0.7370 | 1.0000 / 0.7658 |
| TP / FP / FN | 199 / 0 / 71 | 206 / 0 / 63 |

- Every injected duplicate matched its original at the review tier (25 of 25 per type, both seeds). All but `ssn_substitute` and `dob_day_typo` also reached `likely_duplicate`; those two always land in review.
- No false positive reached `likely_duplicate`. SSN-collision and near-SSN look-alikes were rarely flagged (3 of 196 false positives, 2 of 150).
- An earlier run (56 base patients, 10 per type) had precision 0.7569 and 0.7361, so precision fell as the set grew. That run also changed the injected counts.

**What it gets wrong**
- **Same-DOB strangers.** 67 false positives per seed (34% and 45%) are two original patients sharing a DOB, scoring about 0.45 to 0.48. A DOB match is worth 0.30, so a little name similarity clears the cutoff.
- **Twins.** All 25 injected twins were flagged (25 base-to-twin pairs in seed 42, 26 in seed 7).
- **Compound errors.** Seed 7 missed 3 pairs: two copies of one person, one with a wrong SSN digit and one with a wrong birth day, score 0.42 against each other. Each copy still matches the original.
- **Agent test.** The tool was called 4 of 4, values were exact 4 of 4, results equaled a direct call 4 of 4. The written answers were not always right: one described the SSN difference wrongly ("678 vs 671" for 6781 vs 6718). An earlier run of the same prompts (output not kept) called the near-match pair "very likely different patients"; this run said "very likely the same" and advised review. The tool verdict was identical.

## 5. What a clinic would do with the output
In seed 42, 466 of 414,505 pairs (about 0.1%) needed a person. `likely_duplicate` pairs go to a merge queue with reasons. Review-only pairs need a human to check identifiers the tool does not use (address, phone, MRN); only 71 of 267 (27%) were true duplicates in seed 42, and 60 of 210 (29%) in seed 7.

## 6. Limits
- Synthetic data, one state, one Synthea version. Synthea SSNs share a fixed 999 prefix in the records checked.
- I designed the injected errors and fixed the scorer against similar failures, so recall is optimistic. Look-alikes are 100 of 911 records by my choice, so precision depends on that mix.
- All-pairs comparison grows quadratically; no blocking is implemented.
- Thresholds and weights are hand-set, not validated on real data.
- The nickname list's maintainers say it is heavily biased toward traditionally African American names and may lack others.
- DOBs outside a fixed list of formats are treated as missing; the result says so in its reasons.
- The agent test calls the function directly. The unmatched-results report is verified in demo mode only.
- Sending SSNs through a language-model prompt is unsuitable for real data. Real use needs HIPAA compliance and a BAA where required.

## 7. Repo
[FILL: GitHub link]. File list, run steps and `DATA_DICTIONARY.md` are in the README.
