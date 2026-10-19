"""Minimal Groq client (OpenAI-compatible chat API) over the standard library.

- Enabled only when GROQ_API_KEY is set; otherwise `available` is False and
  callers fall back to deterministic rules, so the prototype runs offline.
- temperature=0 + JSON mode + an on-disk cache keyed on the exact request,
  so evaluation runs are reproducible and do not re-spend rate limits.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "llm"


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, model: str | None = None, use_cache: bool = True):
        self.key = os.environ.get("GROQ_API_KEY", "")
        self.model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
        self.use_cache = use_cache

    @property
    def available(self) -> bool:
        return bool(self.key)

    def json(self, system: str, user: str, retries: int = 3) -> dict:
        """Return the model's reply parsed as a JSON object."""
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        cached = CACHE_DIR / f"{key}.json"
        if self.use_cache and cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
        if not self.available:
            raise LLMError("GROQ_API_KEY is not set")

        req = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/json",
                     "User-Agent": "reqtrace/0.1"})
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    payload = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if e.code == 429 and attempt < retries - 1:   # rate limited
                    time.sleep(2 ** attempt * 5)
                    continue
                raise LLMError(f"Groq HTTP {e.code} (model={self.model}): {detail}") from e
            except urllib.error.URLError as e:
                raise LLMError(f"Groq unreachable: {e.reason}") from e

        text = payload["choices"][0]["message"]["content"]
        try:
            out = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"model did not return JSON: {text[:200]}") from e
        if self.use_cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        return out


def quote_in(quote: str, source: str) -> bool:
    """Guardrail: an AI 'evidence' quote must appear verbatim in the source."""
    norm = lambda s: " ".join(s.lower().split()).strip(" .\"'")
    q = norm(quote)
    return bool(q) and q in norm(source)
