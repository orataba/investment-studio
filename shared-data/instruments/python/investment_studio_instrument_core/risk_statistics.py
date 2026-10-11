"""Shared empirical loss statistics; callers own economic and sample eligibility."""
from math import ceil, floor, isfinite


def empirical_tail(losses: list[float], confidence: float) -> dict[str, object]:
    """Inverse empirical CDF VaR and integrated upper-tail ES, including atoms.

    Averaging `loss >= VaR` incorrectly changes the requested tail probability
    for ties and finite samples. Fractional boundary mass preserves it exactly.
    """
    if not 0 < confidence < 1:
        raise ValueError("Confidence must be strictly between zero and one.")
    try:
        ordered = sorted(float(value) for value in losses if not isinstance(value, bool))
    except (TypeError, ValueError) as error:
        raise ValueError("Historical losses must be finite numbers.") from error
    if len(ordered) != len(losses) or any(not isfinite(value) for value in ordered):
        raise ValueError("Historical losses must be finite numbers.")
    count = len(ordered)
    mass = count * (1 - confidence)
    if abs(mass - round(mass)) < 1e-10:
        mass = float(round(mass))
    result = {"observation_count": count, "tail_effective_observations": mass,
              "tail_observation_count": ceil(mass),
              "tail_max_observation_weight": min(1.0, 1 / mass) if mass else None,
              "var": None, "expected_shortfall": None}
    # Below one scenario of tail mass, empirical VaR/ES would both use only the
    # maximum loss. Suppress that unresolved tail by product policy; the empirical
    # quantile is mathematically defined. Larger samples do not imply adequacy.
    if mass < 1:
        return result
    rank = ceil(confidence * count - 1e-10) - 1
    descending = ordered[::-1]
    whole = floor(mass)
    tail_sum = sum(descending[:whole])
    fraction = mass - whole
    if fraction:
        tail_sum += fraction * descending[whole]
    return {**result, "var": ordered[max(rank, 0)], "expected_shortfall": tail_sum / mass}
