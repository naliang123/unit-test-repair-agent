"""Pure functions with explicit boundary behavior for mutation testing."""


def clamp(value: float, low: float, high: float) -> float:
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(value, high))


def discount_price(price: float, percent: float) -> float:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (1 - percent / 100), 2)


def moving_average(values: list[float], window: int) -> list[float]:
    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window:
        return []
    return [
        sum(values[i : i + window]) / window
        for i in range(len(values) - window + 1)
    ]


def chunked(items: list[int], size: int) -> list[list[int]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i : i + size] for i in range(0, len(items), size)]


def fibonacci(n: int) -> list[int]:
    if n < 0:
        raise ValueError("n must be non-negative")
    sequence = [0, 1]
    for _ in range(2, n + 1):
        sequence.append(sequence[-1] + sequence[-2])
    return sequence[: n + 1]


def parse_score(raw: object) -> float:
    try:
        score = float(raw)
    except (TypeError, ValueError):
        raise ValueError("invalid score") from None
    if not 0 <= score <= 100:
        raise ValueError("score out of range")
    return score


def max_difference(values: list[float]) -> float:
    if len(values) < 2:
        return 0
    return max(values) - min(values)


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def every_other(items: list[int], start: int = 0) -> list[int]:
    return list(items[start::2])


def cumulative_sum(values: list[float]) -> list[float]:
    total = 0
    result = []
    for value in values:
        total += value
        result.append(total)
    return result
