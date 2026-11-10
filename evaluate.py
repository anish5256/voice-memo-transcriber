#!/usr/bin/env python3
"""Score a transcript against a human-corrected reference (WER and CER).

  python evaluate.py hyp.txt gold.txt            # raw spelling
  python evaluate.py hyp.txt gold.txt --normalize  # merge spelling variants of the same word

Timestamps and "Speaker N:" tags are ignored; text is compared as one stream, lower-cased,
punctuation removed.
"""
import argparse
import re

from hinglish_norm import tokens, strip_tags


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(hyp, ref, normalize):
    h, r = tokens(hyp, normalize), tokens(ref, normalize)
    return edit_distance(h, r) / max(1, len(r))


def cer(hyp, ref, normalize):
    h, r = " ".join(tokens(hyp, normalize)), " ".join(tokens(ref, normalize))
    return edit_distance(h, r) / max(1, len(r))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("hyp")
    ap.add_argument("ref")
    ap.add_argument("--normalize", action="store_true")
    a = ap.parse_args()
    hyp, ref = open(a.hyp, encoding="utf-8").read(), open(a.ref, encoding="utf-8").read()
    print(f"WER {wer(hyp, ref, a.normalize) * 100:.1f}%   CER {cer(hyp, ref, a.normalize) * 100:.1f}%"
          f"   (normalize={a.normalize}, ref words={len(tokens(ref, a.normalize))})")
