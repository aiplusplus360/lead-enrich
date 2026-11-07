"""String similarity for record matching.

Two measures, for two different failure modes:

* Jaro-Winkler handles typos and truncation, and rewards a shared prefix, which
  is what makes ``Jon`` / ``Jonathan`` and ``Acme Analytics`` / ``Acme Analytix``
  line up. Raw Levenshtein does not, because a missing prefix costs the same as
  any other edit.
* Token-set Dice handles reordering and extra tokens, which is what makes
  ``Smith John`` and ``John Smith, PhD`` line up. Edit distance sees those as
  almost unrelated strings.

Taking the maximum of the two means neither failure mode wins. Taking the
average would let one bad measure drag down a confident one.
"""

from __future__ import annotations

from typing import Iterable, List, Set

__all__ = ["jaro", "jaro_winkler", "token_set_ratio", "best_similarity", "tokens"]


def jaro(a: str, b: str) -> float:
    """Jaro similarity in [0, 1]."""
    a, b = a or "", b or ""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0

    window = max(0, max(la, lb) // 2 - 1)
    a_matched = [False] * la
    b_matched = [False] * lb
    matches = 0

    for i, char in enumerate(a):
        start = max(0, i - window)
        end = min(i + window + 1, lb)
        for j in range(start, end):
            if not b_matched[j] and b[j] == char:
                a_matched[i] = True
                b_matched[j] = True
                matches += 1
                break

    if not matches:
        return 0.0

    transpositions = 0
    k = 0
    for i in range(la):
        if not a_matched[i]:
            continue
        while not b_matched[k]:
            k += 1
        if a[i] != b[k]:
            transpositions += 1
        k += 1

    transpositions //= 2
    return (matches / la + matches / lb + (matches - transpositions) / matches) / 3.0


def jaro_winkler(a: str, b: str, *, prefix_scale: float = 0.1, boost_threshold: float = 0.7) -> float:
    """Jaro-Winkler with the standard 4-character prefix cap.

    The boost only applies above ``boost_threshold``, so genuinely dissimilar
    strings that happen to share a prefix are not pushed over a match threshold.
    """
    score = jaro(a, b)
    if score <= boost_threshold:
        return score
    prefix = 0
    for left, right in zip(a, b):
        if left != right or prefix == 4:
            break
        prefix += 1
    return min(1.0, score + prefix * prefix_scale * (1.0 - score))


def tokens(text: str) -> Set[str]:
    return {t for t in (text or "").lower().replace(",", " ").split() if t}


def token_set_ratio(a: str, b: str) -> float:
    """Dice coefficient over token sets."""
    left, right = tokens(a), tokens(b)
    if not left or not right:
        return 0.0
    return 2.0 * len(left & right) / (len(left) + len(right))


def best_similarity(a: str, b: str) -> float:
    """Max of Jaro-Winkler and token-set Dice, on normalised input."""
    left = " ".join(sorted(tokens(a))) if a else ""
    right = " ".join(sorted(tokens(b))) if b else ""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return max(jaro_winkler(left, right), token_set_ratio(left, right))
