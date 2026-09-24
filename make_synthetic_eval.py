"""
make_synthetic_eval.py

Builds a labeled evaluation set from Synthea FHIR output and scores
patient_match.match_patients against it.

Steps:
  1. Load base patients from Synthea FHIR bundles (synthetic only).
  2. Inject labeled duplicates (same entity, corrupted) and hard negatives
     (different entity that looks similar).
  3. Compare EVERY pair of records, so precision/recall come from real
     ground truth over all comparisons, not a hand-picked set.

Everything is seeded. The seed and counts are printed and saved so the run
can be reproduced. Nothing here uses course data or employer data.

Run:
    python3 make_synthetic_eval.py --fhir-dir fhir --seed 42 --n-each 10

Notes:
  - Printed records show SSNs masked to the last four digits. The CSVs written
    to --out contain full synthetic SSNs, including some in the normal
    assignable range (look-alike types), so keep them out of version control.
  - The hard-negative types (twin, ssn_collision, near_ssn, same_dob)
    mirror the gap types you already fixed, so results on them are NOT an
    independent test of those fixes. Report per-type numbers, not just totals.
  - The nickname list below is hand-written and small on purpose, so it does
    not depend on names.csv.
"""

import argparse
import csv
import glob
import json
import os
import random
import re
import time
from collections import defaultdict

from patient_match import match_patients

# Small hand-written nickname map (formal -> nickname). Own data, no source to cite.
NICKNAMES = {
    "robert": "bob", "william": "bill", "elizabeth": "liz", "michael": "mike",
    "james": "jim", "katherine": "kate", "margaret": "peggy", "richard": "rick",
    "jennifer": "jen", "christopher": "chris", "thomas": "tom", "patricia": "pat",
    "joseph": "joe", "daniel": "dan", "matthew": "matt", "samuel": "sam",
}


# ---------- loading Synthea ----------

def clean_name(s):
    # Synthea appends digits to names (e.g. "Jaime877"); strip them.
    return re.sub(r"[^A-Za-z'-]", "", s or "").strip()


def extract_ssn(patient):
    for ident in patient.get("identifier", []):
        system = (ident.get("system") or "").lower()
        codes = [c.get("code") for c in ident.get("type", {}).get("coding", [])]
        if "us-ssn" in system or "SS" in codes:
            return ident.get("value")
    return None


def load_base_patients(fhir_dir):
    files = [
        f for f in glob.glob(os.path.join(fhir_dir, "*.json"))
        if "hospitalInformation" not in f and "practitionerInformation" not in f
    ]
    files.sort()
    patients = []
    skipped = 0
    for f in files:
        with open(f) as fh:
            bundle = json.load(fh)
        pat = next(
            (e["resource"] for e in bundle.get("entry", [])
             if e.get("resource", {}).get("resourceType") == "Patient"),
            None,
        )
        if not pat:
            skipped += 1
            continue
        names = pat.get("name", [])
        nm = next((n for n in names if n.get("use") == "official"), names[0] if names else None)
        ssn = extract_ssn(pat)
        dob = pat.get("birthDate")
        if not (nm and ssn and dob):
            skipped += 1
            continue
        first = clean_name((nm.get("given") or [""])[0])
        last = clean_name(nm.get("family"))
        if not first or not last:
            skipped += 1
            continue
        patients.append({"first_name": first, "last_name": last, "dob": dob, "ssn": ssn})
    return patients, len(files), skipped


# ---------- corruption helpers ----------

def digits(ssn):
    return re.sub(r"\D", "", ssn)


def fmt_ssn(d):
    return f"{d[:3]}-{d[3:5]}-{d[5:]}"


def typo(rng, s):
    if len(s) < 3:
        return s
    i = rng.randrange(len(s))
    op = rng.choice(["sub", "del", "ins"])
    letters = "abcdefghijklmnopqrstuvwxyz"
    if op == "sub":
        c = rng.choice([x for x in letters if x != s[i].lower()])
        return s[:i] + c + s[i + 1:]
    if op == "del":
        return s[:i] + s[i + 1:]
    return s[:i] + rng.choice(letters) + s[i:]


def transpose_ssn(rng, ssn):
    d = digits(ssn)
    idx = [i for i in range(len(d) - 1) if d[i] != d[i + 1]]
    if not idx:
        return ssn
    i = rng.choice(idx)
    d = d[:i] + d[i + 1] + d[i] + d[i + 2:]
    return fmt_ssn(d)


def substitute_ssn_digit(rng, ssn):
    d = digits(ssn)
    i = rng.randrange(len(d))
    new = rng.choice([x for x in "0123456789" if x != d[i]])
    return fmt_ssn(d[:i] + new + d[i + 1:])


def random_ssn(rng, used):
    while True:
        d = "".join(rng.choice("0123456789") for _ in range(9))
        if d[:3] not in ("000", "666") and not d.startswith("9") and d not in used:
            return fmt_ssn(d)


def shift_dob_day(rng, dob):
    y, m, d = dob.split("-")
    d = int(d)
    d = d + 1 if d < 28 else d - 1
    return f"{y}-{m}-{d:02d}"


def shift_dob_years(dob, years):
    y, m, d = dob.split("-")
    if m == "02" and d == "29":
        d = "28"
    return f"{int(y) + years}-{m}-{d}"


# ---------- building the labeled dataset ----------

POSITIVE_KINDS = [
    "exact_dup", "typo_first", "typo_last", "ssn_transpose", "ssn_substitute",
    "dob_day_typo", "name_swap", "nickname", "masked_ssn",
]
NEGATIVE_KINDS = ["twin", "ssn_collision", "near_ssn", "same_dob"]


def build_dataset(base, rng, n_each):
    records = []
    for i, p in enumerate(base):
        records.append({**p, "entity": i, "kind": "base"})
    next_entity = len(base)
    used_ssn = {digits(p["ssn"]) for p in base}

    def pick(n, pool=None):
        pool = pool if pool is not None else list(range(len(base)))
        return rng.sample(pool, min(n, len(pool)))

    injected = defaultdict(int)

    # Nickname test needs formal first names. Synthea names rarely match the
    # small NICKNAMES map, so overwrite first names on a seeded sample of base
    # patients (still synthetic) BEFORE any copies are made.
    nick_idx = rng.sample(range(len(base)), min(n_each, len(base)))
    for i in nick_idx:
        formal = rng.choice(sorted(NICKNAMES)).capitalize()
        base[i]["first_name"] = formal
        records[i]["first_name"] = formal

    # --- positives: same entity, corrupted copy ---
    for kind in POSITIVE_KINDS:
        pool = None
        if kind == "nickname":
            pool = nick_idx
        for i in pick(n_each, pool):
            p = dict(base[i])
            if kind == "typo_first":
                p["first_name"] = typo(rng, p["first_name"])
            elif kind == "typo_last":
                p["last_name"] = typo(rng, p["last_name"])
            elif kind == "ssn_transpose":
                p["ssn"] = transpose_ssn(rng, p["ssn"])
            elif kind == "ssn_substitute":
                p["ssn"] = substitute_ssn_digit(rng, p["ssn"])
            elif kind == "dob_day_typo":
                p["dob"] = shift_dob_day(rng, p["dob"])
            elif kind == "name_swap":
                p["first_name"], p["last_name"] = p["last_name"], p["first_name"]
            elif kind == "nickname":
                p["first_name"] = NICKNAMES[p["first_name"].lower()].capitalize()
            elif kind == "masked_ssn":
                p["ssn"] = "XXX-XX-" + digits(p["ssn"])[-4:]
            records.append({**p, "entity": i, "kind": kind})
            injected[kind] += 1

    # --- hard negatives: different entity that looks similar ---
    for kind in NEGATIVE_KINDS:
        for i in pick(n_each):
            src = base[i]
            if kind == "twin":
                # same last name + DOB, different first name, different SSN
                others = [b["first_name"] for b in base if b["first_name"] != src["first_name"]]
                p = {"first_name": rng.choice(others), "last_name": src["last_name"],
                     "dob": src["dob"], "ssn": random_ssn(rng, used_ssn)}
            elif kind == "ssn_collision":
                # identical SSN, different person, DOB 5+ years apart
                other = base[rng.randrange(len(base))]
                p = {"first_name": other["first_name"], "last_name": other["last_name"],
                     "dob": shift_dob_years(src["dob"], 5 + rng.randrange(20)),
                     "ssn": src["ssn"]}
            elif kind == "near_ssn":
                # different person: new first/last combination, different DOB,
                # SSN one digit off from src's SSN
                fa, la, da = (base[rng.randrange(len(base))] for _ in range(3))
                p = {"first_name": fa["first_name"], "last_name": la["last_name"],
                     "dob": shift_dob_years(da["dob"], 1 + rng.randrange(10)),
                     "ssn": substitute_ssn_digit(rng, src["ssn"])}
            else:  # same_dob
                other = base[rng.randrange(len(base))]
                p = {"first_name": other["first_name"], "last_name": other["last_name"],
                     "dob": src["dob"], "ssn": random_ssn(rng, used_ssn)}
            records.append({**p, "entity": next_entity, "kind": kind})
            next_entity += 1
            injected[kind] += 1

    return records, dict(injected)


# ---------- scoring ----------

def strip(r):
    return {k: r[k] for k in ("first_name", "last_name", "dob", "ssn")}


def masked(r):
    """Record for printing: SSN masked to its last four digits, so printed
    results never contain a full SSN-shaped number."""
    d = strip(r)
    digits_ = re.sub(r"\D", "", d["ssn"])
    d["ssn"] = "XXX-XX-" + digits_[-4:] if len(digits_) >= 4 else "XXX-XX-XXXX"
    return d


def safe_div(a, b):
    return a / b if b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fhir-dir", default="fhir")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-each", type=int, default=10, help="records injected per type")
    ap.add_argument("--out", default="eval_output")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    base, n_files, skipped = load_base_patients(args.fhir_dir)
    if not base:
        raise SystemExit("No usable patients found. Check --fhir-dir and that Synthea wrote SSNs.")

    records, injected = build_dataset(base, rng, args.n_each)
    n = len(records)
    total_pairs = n * (n - 1) // 2

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "records_labels.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "entity", "kind", "first_name", "last_name", "dob", "ssn"])
        for i, r in enumerate(records):
            w.writerow([i, r["entity"], r["kind"], r["first_name"], r["last_name"], r["dob"], r["ssn"]])

    # every pair
    t0 = time.perf_counter()
    counts = {"review": defaultdict(int), "likely": defaultdict(int)}
    tp_kind = {"review": defaultdict(int), "likely": defaultdict(int)}
    true_kind_total = defaultdict(int)
    fp_kind = {"review": defaultdict(int), "likely": defaultdict(int)}
    misses, false_pos = [], []
    flagged_rows = []

    for i in range(n):
        for j in range(i + 1, n):
            a, b = records[i], records[j]
            truth = a["entity"] == b["entity"]
            res = match_patients(strip(a), strip(b))
            pred_review = res.verdict != "not_a_match"
            pred_likely = res.verdict == "likely_duplicate"
            kind = (a["kind"] if a["kind"] != "base" else b["kind"]) if truth else None
            if truth:
                true_kind_total[kind] += 1
            for name, pred in (("review", pred_review), ("likely", pred_likely)):
                if truth and pred:
                    counts[name]["tp"] += 1
                    tp_kind[name][kind] += 1
                elif truth and not pred:
                    counts[name]["fn"] += 1
                elif (not truth) and pred:
                    counts[name]["fp"] += 1
                    fp_kind[name][f"{a['kind']}|{b['kind']}"] += 1
                else:
                    counts[name]["tn"] += 1
            if truth and not pred_review:
                misses.append((i, j, a["kind"], b["kind"], res.score, res.verdict, res.reasons))
            if (not truth) and pred_review:
                false_pos.append((i, j, a["kind"], b["kind"], res.score, res.verdict, res.reasons))
            if pred_review:
                flagged_rows.append([i, j, int(truth), res.score, res.verdict, "; ".join(res.reasons)])
    elapsed = time.perf_counter() - t0

    with open(os.path.join(args.out, "flagged_pairs.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx_a", "idx_b", "is_true_duplicate", "score", "verdict", "reasons"])
        w.writerows(sorted(flagged_rows, key=lambda r: -r[3]))

    # ---------- report ----------
    print("=" * 70)
    print(f"seed={args.seed}  n_each={args.n_each}")
    print(f"Synthea bundle files found: {n_files}   skipped (no Patient/SSN/name/DOB): {skipped}")
    print(f"Base patients used: {len(base)}")
    print(f"Injected records: {sum(injected.values())}  by type: {injected}")
    print(f"Total records: {n}   pairs compared: {total_pairs}")
    print(f"Scan time: {elapsed:.2f}s")
    print("=" * 70)
    for name, label in (("review", "Flagged = likely OR possible (review)"),
                        ("likely", "Flagged = likely_duplicate only")):
        c = counts[name]
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        p = safe_div(tp, tp + fp)
        r = safe_div(tp, tp + fn)
        print(f"\n{label}")
        print(f"  TP={tp}  FP={fp}  FN={fn}  TN={c['tn']}")
        print(f"  precision={'n/a' if p is None else f'{p:.4f}'}   recall={'n/a' if r is None else f'{r:.4f}'}")
    base_idx = {r["entity"]: i for i, r in enumerate(records) if r["kind"] == "base"}
    per_type = defaultdict(lambda: [0, 0, 0])  # injected, flagged(review+), flagged(likely)
    for k, r in enumerate(records):
        if r["kind"] in POSITIVE_KINDS:
            res = match_patients(strip(records[base_idx[r["entity"]]]), strip(r))
            per_type[r["kind"]][0] += 1
            per_type[r["kind"]][1] += res.verdict != "not_a_match"
            per_type[r["kind"]][2] += res.verdict == "likely_duplicate"
    print("\nRecall by injected type, each injected record vs its own base record")
    print("  (review threshold / likely-only threshold):")
    for kind in POSITIVE_KINDS:
        t, rv, lk = per_type[kind]
        print(f"  {kind:15s} {rv}/{t}   |   {lk}/{t}")
    print("\nFalse positives by record-kind pair (review threshold):")
    for k, v in sorted(fp_kind["review"].items(), key=lambda x: -x[1]):
        print(f"  {k:30s} {v}")
    print(f"\nMissed true duplicates (review threshold): {len(misses)} (first 10)")
    for m in misses[:10]:
        print(f"  {m[2]}|{m[3]} score={m[4]} verdict={m[5]} reasons={m[6]}")
        print(f"     A: {masked(records[m[0]])}")
        print(f"     B: {masked(records[m[1]])}")
    print(f"\nFalse positives (review threshold): {len(false_pos)} (first 10)")
    for fpz in false_pos[:10]:
        print(f"  {fpz[2]}|{fpz[3]} score={fpz[4]} verdict={fpz[5]} reasons={fpz[6]}")
    print(f"\nSaved: {args.out}/records_labels.csv, {args.out}/flagged_pairs.csv")


if __name__ == "__main__":
    main()
