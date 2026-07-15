"""Personal dictionary: user-listed terms (one per line in ~/.sotto/dictionary.txt).

Applied twice: fuzzy replacement over the raw transcript fixes ASR misspellings,
and the term list is injected into the cleaning prompt so the LLM keeps exact
spellings (and doesn't "correct" jargon away).
"""

import os
import re

from rapidfuzz import fuzz


class Dictionary:
    def __init__(self, path: str, threshold: int = 85):
        self.path = path
        self.threshold = threshold
        self.terms: list[str] = []
        self.reload()

    def reload(self):
        self.terms = []
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.terms = [line.strip() for line in f
                              if line.strip() and not line.startswith("#")]

    def apply(self, text: str) -> str:
        """Replace token n-grams that fuzzy-match a dictionary term with the exact term.

        A term of n tokens is matched against windows of n-1, n, and n+1 tokens
        (an ASR mishearing can split or merge words); the best-scoring window wins.
        """
        if not self.terms or not text:
            return text
        tokens = re.findall(r"\S+", text)
        for term in self.terms:
            n = len(term.split())
            term_low = term.lower()
            sizes = sorted({max(1, n - 1), n, n + 1}, reverse=True)
            i = 0
            while i < len(tokens):
                best_size, best_score = 0, 0.0
                for size in sizes:
                    if i + size > len(tokens):
                        continue
                    window_clean = " ".join(tokens[i:i + size]).strip(".,!?;:").lower()
                    if window_clean == term_low:
                        best_size, best_score = 0, 0.0
                        i += size - 1
                        break
                    score = fuzz.ratio(window_clean, term_low)
                    if score >= self.threshold and score > best_score:
                        best_size, best_score = size, score
                if best_size:
                    window = " ".join(tokens[i:i + best_size])
                    trailing = window[len(window.rstrip(".,!?;:")):]
                    tokens[i:i + best_size] = [term + trailing]
                i += 1
        return " ".join(tokens)
