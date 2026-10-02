"""Preparation failures that can use the research runner's bounded retry policy."""


class ResearchInputUnavailable(OSError):
    """A required upstream input could not be read because of a transient failure."""
