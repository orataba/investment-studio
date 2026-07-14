from __future__ import annotations

from typing import Literal, TypeAlias, cast


PortfolioOperatingProfile: TypeAlias = Literal[
    "standard_taxonomy",
    "external_etf_rotation",
]

SUPPORTED_PORTFOLIO_OPERATING_PROFILES: tuple[PortfolioOperatingProfile, ...] = (
    "standard_taxonomy",
    "external_etf_rotation",
)

ALLOCATION_RESEARCH_OPERATING_PROFILE: PortfolioOperatingProfile = (
    "standard_taxonomy"
)


class OperatingProfileCapabilityError(ValueError):
    def __init__(
        self,
        *,
        operating_profile: PortfolioOperatingProfile,
        capability: str,
    ) -> None:
        self.operating_profile = operating_profile
        self.capability = capability
        super().__init__(
            f"{capability} is not applicable to operating_profile "
            f"'{operating_profile}'."
        )


def require_portfolio_operating_profile(
    value: object,
    *,
    context: str,
) -> PortfolioOperatingProfile:
    if (
        not isinstance(value, str)
        or value not in SUPPORTED_PORTFOLIO_OPERATING_PROFILES
    ):
        supported = ", ".join(SUPPORTED_PORTFOLIO_OPERATING_PROFILES)
        raise ValueError(f"{context} operating_profile must be one of: {supported}.")
    return cast(PortfolioOperatingProfile, value)


def supports_allocation_research(value: object) -> bool:
    return (
        require_portfolio_operating_profile(value, context="Portfolio")
        == ALLOCATION_RESEARCH_OPERATING_PROFILE
    )


def require_allocation_research_profile(value: object) -> None:
    profile = require_portfolio_operating_profile(value, context="Portfolio")
    if profile != ALLOCATION_RESEARCH_OPERATING_PROFILE:
        raise OperatingProfileCapabilityError(
            operating_profile=profile,
            capability="Allocation Research",
        )
