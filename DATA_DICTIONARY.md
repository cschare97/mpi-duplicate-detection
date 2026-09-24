# Data Dictionary

All data used with this project is synthetic. No real patient data, and no data from any employer or course, is included. Every value below was taken from the code in this repo. If code and this file disagree, the code is right.

## 1. Input record (`match_patients`)

`match_patients(record_a, record_b)` takes two dicts. Keys are matched case-insensitively, spaces treated as underscores. Missing keys are allowed.

| Field | Type | Used in scoring | Notes |
|---|---|---|---|
| `last_name` | string | yes | Letters only after normalization, lowercased |
| `first_name` | string | yes | Same normalization; nickname variants score 1.0 |
| `dob` | string or datetime | yes | Accepted string formats: `Mon DD, YYYY`, `Month DD, YYYY`, `Mon DD YYYY`, `Month DD YYYY`, `DD Mon YYYY`, `DD Month YYYY`, `YYYY-MM-DD`, `YYYY/MM/DD`, `MM/DD/YYYY` (month first). Periods are ignored and `Sept` is read as `Sep`. Any other format is treated as missing data, and the result says so |
| `ssn` | string | yes | Digits only after normalization. `X` mask characters are dropped |
| `account_number`, `mrn` | any | no | Accepted but not used |

## 2. Output (`MatchResult`)

| Field | Type | Meaning |
|---|---|---|
| `score` | float | 0 to 1, rounded to 3 decimals |
| `verdict` | string | `likely_duplicate` (score >= 0.75), `possible_duplicate_review` (0.45 to below 0.75), `not_a_match` (below 0.45) |
| `reasons` | list of strings | Human-readable evidence behind the score |
| `detail` | dict | `ssn_relation`, `dob_relation`, `last_name_similarity`, `first_name_similarity`, `name_swap_detected` |

### Score weights

| Signal | Weight |
|---|---|
| SSN | 0.40 |
| DOB | 0.30 |
| Last name | 0.18 |
| First name | 0.12 |

### `ssn_relation` values and the confidence each contributes

| Value | Confidence | When |
|---|---|---|
| `exact` | 1.0 | Same digits |
| `masked_match` | 0.85 | All unmasked digits agree, same length |
| `transposition` | 0.75 | Same digits with one adjacent pair swapped |
| `near_typo` | 0.3 | Same length, Levenshtein distance 1 or 2 |
| `collision_suspected` | 0.2 | Exact SSN match but DOBs more than 3 years apart |
| `mismatch` | 0.0 | None of the above |
| `insufficient_data` | 0.0 | One or both SSNs empty |

### `dob_relation` values

| Value | Confidence |
|---|---|
| `exact` | 1.0 |
| `mismatch` | 0.0 |
| `insufficient_data` | 0.0 (one or both DOBs missing or unreadable; the reasons list says so) |

### Other constants

| Constant | Value | Purpose |
|---|---|---|
| `SWAP_SIMILARITY_FLOOR` | 0.70 | Both cross-aligned name similarities must reach this before a first/last field swap is accepted |
| `COLLISION_DOB_GAP_DAYS` | 1095 (3 years) | DOB gap that turns an exact SSN match into `collision_suspected` |

Name similarity uses Jaro-Winkler. SSN near-match uses Levenshtein distance.

## 3. Nickname data (`names.csv`)

| Column | Meaning |
|---|---|
| `name1` | Formal name |
| `relationship` | Only `has_nickname` is used |
| `name2` | Nickname of `name1` |

Two names count as variants only if one is listed as a nickname of the other, or both are nicknames of the same formal name. Source and license: see the attribution section of the README.

## 4. Evaluation outputs (`make_synthetic_eval.py`)

`records_labels.csv`

| Column | Meaning |
|---|---|
| `idx` | Row index in the generated dataset |
| `entity` | Ground-truth person id. Same `entity` means the same person |
| `kind` | `base`, an injected duplicate type, or an injected look-alike type |
| `first_name`, `last_name`, `dob`, `ssn` | Record values (synthetic) |

Injected duplicate types: `exact_dup`, `typo_first`, `typo_last`, `ssn_transpose`, `ssn_substitute`, `dob_day_typo`, `name_swap`, `nickname`, `masked_ssn`.
Injected look-alike (different person) types: `twin`, `ssn_collision`, `near_ssn`, `same_dob`.

`flagged_pairs.csv` (pairs with verdict other than `not_a_match`)

| Column | Meaning |
|---|---|
| `idx_a`, `idx_b` | Row indexes in `records_labels.csv` |
| `is_true_duplicate` | 1 if both rows share an `entity`, else 0 |
| `score`, `verdict`, `reasons` | From `match_patients` |

## 5. Known limitations

- A DOB in an unsupported format is treated as missing and adds nothing to the score. The reasons list says "DOB missing or unreadable". Two-digit years and day-first numeric dates such as `17/02/1979` are not supported.
- Synthea SSNs in the base data start with 999, so SSN values are not representative of real SSNs.
- The look-alike types mirror scoring gaps that were fixed during development, so they are not an independent test of those fixes.

## 6. MCP tool (`mpi_dedup_server.py`)

Tool `check_duplicate` takes `last_name_a`, `first_name_a`, `dob_a`, `ssn_a`, `last_name_b`, `first_name_b`, `dob_b`, `ssn_b` (all strings) and returns `score`, `verdict`, `reasons`, `detail`.

## 7. Unmatched-result report (`unmatched_results_report.py`)

Reads OpenEMR's `pnotes` table (columns used: `id`, `date`, `body`, `pid`, `assigned_to`, `message_status`; filter `title = 'Lab Results'`). Read-only.

CSV columns written by `--csv`: `pnote_id`, `date`, `skeleton_pid`, `name`, `dob`, `assigned_to`, `message_status`.
