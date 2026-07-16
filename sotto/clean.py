"""The mandatory cleaning & formatting stage.

Three sub-stages: (A) deterministic regex pre-pass, (B) LLM pass on Ollama,
(C) guardrails. Output is never raw ASR text and the stage never blocks:
on LLM timeout/failure the regex-cleaned text is emitted instead.
"""

import re

import requests

SYSTEM_PROMPT = """You are a dictation post-processor. Turn the user's raw speech
transcript into clean written text that reads as exactly what the speaker said.

REQUIRED edits — always perform ALL of these:
1. Remove filler sounds (um, uh, er, hmm), stutters and false starts, and
   verbal tics ("you know", "I mean", "like") where they carry no meaning.
2. Resolve self-corrections: when the speaker revises themselves, keep ONLY
   the final version and delete both the abandoned words and the correction
   phrases ("wait no", "actually", "I mean", "no wait").
3. Add punctuation, capitalization, and paragraph breaks (new paragraph on
   topic change).
4. Format dictated lists ("first... second..." or "bullet point X") as
   "- " list items.
5. Apply spoken punctuation/formatting commands ("comma", "new line",
   "quote ... end quote") instead of writing them out.
6. Spell dictionary terms exactly as given.

FORBIDDEN edits — faithfulness. You are NOT a summarizer or ghostwriter:
- Never paraphrase, condense, shorten, reorder, or "improve" the wording.
- Keep the speaker's phrasing, sentence structure, hedges ("maybe",
  "I think"), and length. Every sentence they spoke must appear, in their
  own words, minus only the REQUIRED edits above.
- Never add information or answer questions that appear in the transcript.
- The Tone hint affects punctuation and formatting style only — never reword.

Examples:
IN: um so let's meet on tuesday uh wait no friday at 2 pm actually make it 3
OUT: So let's meet on Friday at 3pm.
IN: i was thinking we could maybe move the standup because um it clashes with the daily sync
OUT: I was thinking we could maybe move the standup because it clashes with the daily sync.
IN: three things first update the readme second bump the version third push the release
OUT: Three things:
- Update the readme
- Bump the version
- Push the release

Output ONLY the cleaned text — no preamble, no commentary."""

_FILLER_RE = re.compile(r"\b(um+|uh+|ah+|er+m?|hmm+)\b[,.]?\s*", re.IGNORECASE)
_REPEAT_RE = re.compile(r"\b(\w+)(\s+\1\b)+", re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_PREAMBLE_RE = re.compile(r"^(sure|okay|here('s| is)|certainly|i('m| can| will| have))\b", re.IGNORECASE)


def regex_clean(text: str) -> str:
    text = _FILLER_RE.sub("", text)
    text = _REPEAT_RE.sub(r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


class Cleaner:
    def __init__(self, url: str, model: str, timeout_s: float = 6.0,
                 keep_alive: str | int = "5m"):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.keep_alive = keep_alive

    # Fixed context so Ollama never reloads the model mid-session; 8k tokens
    # comfortably covers a 15-minute dictation (input + output) for a 4B model.
    NUM_CTX = 8192

    def warm(self):
        """Ask Ollama to (re)load the model without generating anything. Called
        when a recording starts so the load overlaps the user speaking; when the
        model is already resident this is near-instant and just refreshes the
        keep_alive countdown."""
        try:
            requests.post(f"{self.url}/api/chat",
                          json={"model": self.model, "messages": [],
                                "keep_alive": self.keep_alive,
                                "options": {"num_ctx": self.NUM_CTX}},
                          timeout=120)
        except requests.RequestException:
            pass

    def clean(self, transcript: str, tone: str, terms: list[str]) -> str:
        pre = regex_clean(transcript)
        if not pre:
            return ""
        out = self._llm_clean(pre, tone, terms)
        return out if out is not None else pre

    def _llm_clean(self, text: str, tone: str, terms: list[str]) -> str | None:
        n_words = len(text.split())
        n_tokens = int(n_words * 1.5) + 64
        # Long dictations need proportionally more generation time (~70 tok/s
        # on a 4B model); the base timeout alone would kill them mid-generation.
        timeout = min(self.timeout_s + n_words * 0.06, 180.0)
        user = f"Tone: {tone}\nDictionary: {', '.join(terms) if terms else '(none)'}\nTranscript: {text}"
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "top_p": 1, "repeat_penalty": 1.05,
                        "num_predict": max(n_tokens, 128), "num_ctx": self.NUM_CTX},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        }
        try:
            r = requests.post(f"{self.url}/api/chat", json=body, timeout=timeout)
            r.raise_for_status()
            out = _THINK_RE.sub("", r.json()["message"]["content"]).strip()
        except (requests.RequestException, KeyError, ValueError):
            return None

        # Guardrail C: reject over-edits/hallucinations, fall back to regex-cleaned.
        # Floor: 0.55 of input length with a 60-char allowance — self-corrections
        # legitimately shrink short utterances a lot, but on long dictation a
        # "concise rewrite" that drops close to half the text is discarded.
        if not out:
            return None
        low = max(0.55 * len(text) - 60, 0.3 * len(text))
        if not (low <= len(out) <= 1.6 * len(text) + 40):
            return None
        if _PREAMBLE_RE.match(out) and not _PREAMBLE_RE.match(text):
            return None
        return out
