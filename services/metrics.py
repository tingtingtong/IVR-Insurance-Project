"""Process-level counters for CloudWatch / Prometheus scrapes."""
from collections import Counter

_counts: Counter = Counter()


def inc(name: str, n: int = 1) -> None:
    _counts[name] += n


def snapshot() -> dict[str, int]:
    return dict(_counts)


def prometheus_text() -> str:
    lines = ["# TYPE ivr_counter counter"]
    for key, val in sorted(_counts.items()):
        lines.append(f"ivr_{key} {val}")
    return "\n".join(lines) + "\n"