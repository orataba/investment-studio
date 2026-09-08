"""Research attribution comes only from the authenticated Studio principal."""
from studio_identity import current_principal


def research_identity() -> dict:
    principal = current_principal()
    return {
        "user_id": principal.user_id,
        "display_name": principal.display_name,
        "team_id": principal.team_id,
        "team_role": principal.team_role,
        "kind": principal.kind,
        "service_id": principal.service_id,
        "mode": "account",
        "local_unrestricted": principal.local_unrestricted,
    }


def run_identity(context: dict) -> dict:
    actor = context.get("research_actor")
    if not actor:
        raise ValueError("旧运行尚未确认发起人，不能继续执行。请重新发起研究。")
    return actor
