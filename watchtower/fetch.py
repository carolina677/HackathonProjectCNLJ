"""Network access. Every request is polite (per-host limiter) and cancellable."""

from dataclasses import dataclass, field


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    html: str
    headers: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        from .recipe.engine import visible_text

        return visible_text(self.html)
