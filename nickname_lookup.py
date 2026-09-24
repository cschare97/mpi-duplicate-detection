"""
nickname_lookup.py

Nickname / name-variant matching from names.csv (name1, relationship, name2).
Data source: carltonnorthern/nicknames (Apache-2.0; see LICENSE file in this repo).

Two names are variants only when the file says so directly:
  1. one is listed as a nickname of the other (bob <-> robert), or
  2. both are listed as nicknames of the same formal name (bob <-> rob, via robert).

Deliberately NOT transitive across more than that. An earlier version used
union-find clustering, which chained unrelated names together through shared
nicknames and put about half of all names in one cluster.
"""

import csv
import os
from collections import defaultdict


def load_nickname_maps(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "names.csv")
    nicks_of = defaultdict(set)    # formal name -> its nicknames
    formals_of = defaultdict(set)  # nickname -> formal names it belongs to
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) < 3 or row[1].strip().lower() != "has_nickname":
                continue
            n1, n2 = row[0].strip().lower(), row[2].strip().lower()
            if n1 and n2 and n1 != n2:
                nicks_of[n1].add(n2)
                formals_of[n2].add(n1)
    return nicks_of, formals_of


_NICKS_OF, _FORMALS_OF = load_nickname_maps()


def are_variants(name1: str, name2: str) -> bool:
    a, b = name1.strip().lower(), name2.strip().lower()
    if not a or not b or a == b:
        return False
    if b in _NICKS_OF.get(a, ()) or a in _NICKS_OF.get(b, ()):
        return True
    return bool(_FORMALS_OF.get(a, set()) & _FORMALS_OF.get(b, set()))
