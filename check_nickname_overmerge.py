"""
check_nickname_overmerge.py

Reproduces the nickname over-merging bug found during evaluation.

An earlier version of nickname_lookup.py grouped names with union-find, so
any chain of shared nicknames merged into one cluster. This script rebuilds
that grouping from names.csv, reports the cluster sizes, and compares the
result with the current rule in nickname_lookup.py (direct nickname, or two
nicknames of the same formal name).

Run: python3 check_nickname_overmerge.py
"""

import csv
import os
from collections import Counter

from nickname_lookup import are_variants

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "names.csv")


def build_union_find(path):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 3 and row[1].strip().lower() == "has_nickname":
                a, b = find(row[0].strip().lower()), find(row[2].strip().lower())
                if a != b:
                    parent[a] = b
    return parent, find


parent, find = build_union_find(PATH)
sizes = Counter(find(n) for n in list(parent))
total = len(parent)
biggest = max(sizes.values())
print(f"distinct names: {total}")
print(f"union-find clusters: {len(sizes)}")
print(f"largest cluster: {biggest} names ({100 * biggest / total:.1f}% of all names)")

print("\nUnrelated pairs (old union-find rule vs current rule):")
for a, b in [("christopher", "elizabeth"), ("katherine", "daniel"), ("margaret", "elizabeth")]:
    print(f"  {a} / {b}: old={find(a) == find(b)}  current={are_variants(a, b)}")
print("\nReal nickname pairs (should stay True under the current rule):")
for a, b in [("robert", "bob"), ("william", "bill"), ("elizabeth", "liz")]:
    print(f"  {a} / {b}: current={are_variants(a, b)}")
