"""RatingService: turn token counts into billable USD.

PLACEHOLDER per-model rates (USD per 1M tokens) — swap for your negotiated
prices. Token types map onto a base rate with a multiplier:
    input          -> input rate  x1
    output         -> output rate x1
    cacheRead      -> input rate  x0.1
    cacheCreation  -> input rate  x1.25   (approx; OTEL doesn't split 1h/5m)
Billed = raw cost x markup.
"""

from __future__ import annotations

DEFAULT_RATES = {
    "claude-fable-5":    (10.0, 50.0),
    "claude-opus-4-8":   (5.0, 25.0),
    "claude-opus-4-7":   (5.0, 25.0),
    "claude-sonnet-5":   (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
}
DEFAULT_RATE = (5.0, 25.0)


class RatingService:
    def __init__(self, rates=None, markup: float = 1.50,
                 cache_read_mult: float = 0.1, cache_write_mult: float = 1.25):
        self.rates = rates or DEFAULT_RATES
        self.markup = markup
        self.cache_read_mult = cache_read_mult
        self.cache_write_mult = cache_write_mult

    def _base(self, model: str):
        for key, rate in self.rates.items():
            if key in (model or ""):
                return rate
        return DEFAULT_RATE

    def raw_cost(self, model: str, token_type: str, tokens: int) -> float:
        inp, outp = self._base(model)
        per_m = (tokens or 0) / 1_000_000.0
        if token_type == "input":
            return per_m * inp
        if token_type == "output":
            return per_m * outp
        if token_type == "cacheRead":
            return per_m * inp * self.cache_read_mult
        if token_type == "cacheCreation":
            return per_m * inp * self.cache_write_mult
        return 0.0

    def billed(self, model: str, token_type: str, tokens: int) -> float:
        return self.raw_cost(model, token_type, tokens) * self.markup
