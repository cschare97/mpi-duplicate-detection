"""
patient_match.py

MPI (Master Patient Index) duplicate-detection tool.
Given two patient records, returns a match score (0-1), a verdict,
and the specific reasons behind the score.

Designed as the core logic behind an MCP tool: match_patients(record_a, record_b) -> dict
"""

from __future__ import annotations
import re
from datetime import datetime
from dataclasses import dataclass, field
from nickname_lookup import are_variants
import jellyfish


# ---------- normalization helpers ----------

def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return re.sub(r"[^A-Za-z]", "", name).strip().lower()


def normalize_ssn(ssn) -> str:
    """Return digits only. Masked chars (X) are dropped, not treated as digits."""
    if ssn is None:
        return ""
    s = str(ssn)
    return re.sub(r"[^0-9]", "", s)


def normalize_dob(dob) -> datetime | None:
    """
    Accepts a datetime, or a string in one of these formats (periods are
    ignored, month names are case-insensitive, 'Sept' is read as 'Sep'):
    'Feb. 17, 1979', 'Feb 17 1979', '17 Feb 1979', 'February 17, 1979',
    '17 February 1979', '1979-02-17', '1979/02/17', '02/17/1979' (month first).
    Anything else returns None, which the caller treats as missing data.
    """
    if dob is None:
        return None
    if isinstance(dob, datetime):
        return dob
    s = str(dob).strip().replace(".", "")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\bsept\b", "Sep", s, flags=re.IGNORECASE)
    for fmt in (
        "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y",
        "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------- comparison primitives ----------

def name_similarity(a: str, b: str) -> float:
    a, b = normalize_name(a), normalize_name(b)
    if not a or not b:
        return 0.0
    return jellyfish.jaro_winkler_similarity(a, b)

def first_name_similarity(a: str, b: str) -> float:
    a_norm, b_norm = normalize_name(a), normalize_name(b)
    if not a_norm or not b_norm:
        return 0.0
    if are_variants(a, b):
        return 1.0  # treat known nickname pairs as a full match
    return jellyfish.jaro_winkler_similarity(a_norm, b_norm)

def ssn_compare(a, b) -> tuple[str, float]:
    """
    Returns (relation, confidence) where relation is one of:
    'exact', 'masked_match', 'transposition', 'near_typo', 'mismatch',
    'insufficient_data'. ('collision_suspected' is applied afterward, in
    match_patients, when an exact match conflicts with the DOB.)
    """
    da, db = normalize_ssn(a), normalize_ssn(b)
    if not da or not db:
        return "insufficient_data", 0.0
    if da == db:
        return "exact", 1.0

    # one or both SSNs partially masked (e.g. "XXX-XX-6780") -> normalize_ssn already
    # stripped the X's, so compare only where both sides have digits, position-aligned
    # from the right (masking is typically on the left in these formats).
    raw_a, raw_b = str(a), str(b)
    if "X" in raw_a.upper() or "X" in raw_b.upper():
        digits_a = re.sub(r"[^0-9X]", "", raw_a.upper())
        digits_b = re.sub(r"[^0-9X]", "", raw_b.upper())
        if len(digits_a) == len(digits_b):
            comparable = [(x, y) for x, y in zip(digits_a, digits_b) if x != "X" and y != "X"]
            if comparable and all(x == y for x, y in comparable):
                return "masked_match", 0.85

    # transposition: same digits, one adjacent pair swapped (e.g. "...86"
    # vs "...68"). This is the single most common SSN data-entry error and,
    # unlike a generic substitution, it's strong evidence of the *same*
    # number mistyped -- not a coincidentally-similar different SSN -- so
    # it earns meaningfully higher confidence than a plain near-typo.
    if len(da) == len(db):
        for i in range(len(da) - 1):
            if (
                da[i] != db[i]
                and da[i] == db[i + 1]
                and da[i + 1] == db[i]
                and da[:i] == db[:i]
                and da[i + 2:] == db[i + 2:]
            ):
                return "transposition", 0.75

    # near-typo: same length, small edit distance.
    # A same-digit substitution is a weak, ambiguous signal on its own -- a
    # 1-2 digit difference is just as consistent with two different people
    # who happen to have close SSNs (e.g. siblings/relatives issued numbers
    # in the same batch) as it is with a data-entry typo of the same
    # number. Keep the confidence low enough that it can never, by itself,
    # combine with an exact name+DOB match to cross the likely_duplicate
    # threshold -- that combination is exactly what two different but
    # related individuals (e.g. twins) would also produce.
    if len(da) == len(db):
        dist = jellyfish.levenshtein_distance(da, db)
        if dist <= 2:
            return "near_typo", 0.3

    return "mismatch", 0.0


def dob_compare(a, b) -> tuple[str, float]:
    da, db = normalize_dob(a), normalize_dob(b)
    if da is None or db is None:
        return "insufficient_data", 0.0
    if da == db:
        return "exact", 1.0
    return "mismatch", 0.0


# ---------- main scoring ----------

WEIGHTS = {
    "ssn": 0.40,
    "dob": 0.30,
    "last_name": 0.18,
    "first_name": 0.12,
}

SSN_CONFIDENCE_FLOOR_FOR_REASON = 0.3  # below this, don't report the reason

# A field-swap interpretation must itself look like a real match, not just
# a better fit than an even worse direct comparison. Two unrelated people
# can have mediocre coincidental cross-similarity (e.g. two ordinary,
# unrelated first/last names that happen to share a few letters) that
# still edges out a near-zero direct comparison -- that's a false swap,
# not two mediocre-but-real name components. Requiring both swapped
# similarities to independently clear a real-match floor (not just win by
# comparison) keeps the swap interpretation reserved for cases where the
# swapped alignment is itself strong evidence, not just the less-bad option.
SWAP_SIMILARITY_FLOOR = 0.70


@dataclass
class MatchResult:
    score: float
    verdict: str
    reasons: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def match_patients(record_a: dict, record_b: dict) -> MatchResult:
    """
    record_a / record_b expected keys (case-insensitive, missing keys OK):
    last_name, first_name, dob, ssn, account_number, mrn
    """
    def get(rec, key):
        for k in rec:
            if k.lower().replace(" ", "_") == key:
                return rec[k]
        return None

    last_a, last_b = get(record_a, "last_name"), get(record_b, "last_name")
    first_a, first_b = get(record_a, "first_name"), get(record_b, "first_name")
    dob_a, dob_b = get(record_a, "dob"), get(record_b, "dob")
    ssn_a, ssn_b = get(record_a, "ssn"), get(record_b, "ssn")

    last_sim = name_similarity(last_a, last_b)
    first_sim = first_name_similarity(first_a, first_b)

    # a first/last field swap (one record's columns got transposed at entry)
    # looks like a total name mismatch under direct positional comparison,
    # even though the underlying identity is a perfect match. Check whether
    # aligning record_a's last name against record_b's first name (and vice
    # versa) fits dramatically better, and use that alignment if so.
    swapped_last_sim = name_similarity(last_a, first_b)
    swapped_first_sim = first_name_similarity(first_a, last_b)
    swap_fits_better = (
        WEIGHTS["last_name"] * swapped_last_sim + WEIGHTS["first_name"] * swapped_first_sim
        > WEIGHTS["last_name"] * last_sim + WEIGHTS["first_name"] * first_sim
    )
    swap_is_itself_strong = (
        swapped_last_sim >= SWAP_SIMILARITY_FLOOR and swapped_first_sim >= SWAP_SIMILARITY_FLOOR
    )
    name_swap_detected = swap_fits_better and swap_is_itself_strong
    if name_swap_detected:
        last_sim, first_sim = swapped_last_sim, swapped_first_sim

    ssn_relation, ssn_conf = ssn_compare(ssn_a, ssn_b)
    dob_relation, dob_conf = dob_compare(dob_a, dob_b)

    # an identical SSN string alongside a DOB that's wildly different (not
    # just off by a plausible data-entry slip of a day/month/year digit) is
    # the signature of an SSN collision -- a transcription error or reused
    # placeholder value -- rather than confirmed shared identity. Don't let
    # a bare digit-string match outrank a DOB conflict that large.
    COLLISION_DOB_GAP_DAYS = 365 * 3
    if ssn_relation == "exact" and dob_relation == "mismatch":
        da, db = normalize_dob(dob_a), normalize_dob(dob_b)
        if da is not None and db is not None and abs((da - db).days) > COLLISION_DOB_GAP_DAYS:
            ssn_relation, ssn_conf = "collision_suspected", 0.2

    score = (
        WEIGHTS["ssn"] * ssn_conf
        + WEIGHTS["dob"] * dob_conf
        + WEIGHTS["last_name"] * last_sim
        + WEIGHTS["first_name"] * first_sim
    )

    reasons = []
    if ssn_relation == "exact":
        reasons.append("SSN exact match")
    elif ssn_relation == "masked_match":
        reasons.append("SSN matches on all unmasked digits")
    elif ssn_relation == "transposition":
        reasons.append("SSN matches except for one transposed digit pair (classic data-entry error)")
    elif ssn_relation == "near_typo":
        reasons.append("SSN differs by 1-2 digits (could be a typo, or a different individual with a similar SSN)")
    elif ssn_relation == "mismatch":
        reasons.append("SSN does not match")
    elif ssn_relation == "collision_suspected":
        reasons.append(
            "SSN string matches exactly, but DOB conflicts by years -- likely an SSN "
            "collision/transcription error, not confirmed shared identity"
        )

    if dob_relation == "exact":
        reasons.append("DOB exact match")
    elif dob_relation == "mismatch":
        reasons.append("DOB does not match")
    elif dob_relation == "insufficient_data":
        reasons.append("DOB missing or unreadable in one or both records (not scored)")

    if name_swap_detected:
        reasons.append(
            f"Possible first/last name field swap detected "
            f"('{last_a} {first_a}' vs '{first_b} {last_b}' aligns much better than a direct field match)"
        )
        if last_sim == 1.0:
            reasons.append(f"Last name field ('{last_a}') exact match to other record's first name field")
        elif last_sim >= 0.90:
            reasons.append(f"Last name field ('{last_a}') near-match to other record's first name field (similarity {last_sim:.2f})")
        if first_sim == 1.0:
            reasons.append(f"First name field ('{first_a}') exact match to other record's last name field")
        elif first_sim >= 0.85:
            reasons.append(f"First name field ('{first_a}') near-match to other record's last name field (similarity {first_sim:.2f})")
    else:
        if last_sim >= 0.90 and normalize_name(last_a) != normalize_name(last_b):
            reasons.append(f"Last name near-match ('{last_a}' vs '{last_b}', similarity {last_sim:.2f})")
        elif last_sim == 1.0:
            reasons.append("Last name exact match")

        if first_sim == 1.0 and normalize_name(first_a) == normalize_name(first_b):
            reasons.append("First name exact match")
        elif first_sim == 1.0:
            reasons.append(f"First name recognized variant ('{first_a}' vs '{first_b}')")
        elif first_sim >= 0.85 and normalize_name(first_a) != normalize_name(first_b):
            reasons.append(f"First name near-match ('{first_a}' vs '{first_b}', similarity {first_sim:.2f})")

    if score >= 0.75:
        verdict = "likely_duplicate"
    elif score >= 0.45:
        verdict = "possible_duplicate_review"
    else:
        verdict = "not_a_match"

    return MatchResult(
        score=round(score, 3),
        verdict=verdict,
        reasons=reasons,
        detail={
            "ssn_relation": ssn_relation,
            "dob_relation": dob_relation,
            "last_name_similarity": round(last_sim, 3),
            "first_name_similarity": round(first_sim, 3),
            "name_swap_detected": name_swap_detected,
        },
    )


if __name__ == "__main__":
    # quick smoke test (invented records, not real people)
    a = {"last_name": "Whitlock", "first_name": "Maren", "dob": "Feb. 17, 1979", "ssn": "999-11-2233"}
    b = {"last_name": "Whitlok", "first_name": "Maren", "dob": "Feb. 17, 1979", "ssn": "999-44-5566"}
    print(match_patients(a, b))

    # nickname variant test
    c = {"last_name": "Smith", "first_name": "Bob", "dob": "Jan 5, 1985", "ssn": "123-45-6789"}
    d = {"last_name": "Smith", "first_name": "Robert", "dob": "Jan 5, 1985", "ssn": "123-45-6789"}
    print(match_patients(c, d))