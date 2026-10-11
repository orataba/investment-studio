"""Exact permission fields, independent of the size of retained research inputs."""

ACCESS_FIELDS = frozenset({"research_actor", "execution", "portfolio_id", "risk_scope"})


def derive_access_context(context):
    if not isinstance(context, dict):
        # Preserve malformed retained scopes so authorization still fails closed.
        return context
    return {key: value for key, value in context.items() if key in ACCESS_FIELDS}
