"""Published editions are public; report production and retained inputs are private."""
from studio_identity import IdentityError, Principal, current_principal


def require_report_access(report, *, write=False):
    principal = current_principal()
    if principal.team_id != report.team_id:
        raise IdentityError(404, "报告不存在")
    if principal.resource_scope and principal.resource_scope != {"kind": "report", "id": report.report_id}:
        raise IdentityError(404, "报告不存在")
    if principal.kind == "service":
        if not (principal.has_scope("briefing:publish") or (not write and principal.has_scope("briefing:read"))):
            raise IdentityError(403, "此服务身份没有报告权限")
    elif write and (principal.team_role == "reader" or (
        report.input_json.get("edition_role") == "publisher" and principal.team_role != "admin"
    )):
        raise IdentityError(403, "没有生成或发布报告的权限")
    return principal


def can_generate(principal: Principal | None, edition_role: str) -> bool:
    if principal is None or principal.resource_scope:
        return False
    if principal.kind == "service":
        return principal.has_scope("briefing:publish")
    return principal.team_role == "admin" or (edition_role == "preview" and principal.team_role == "member")


def is_published(report) -> bool:
    return report.status == "completed" and report.input_json.get("edition_role") == "publisher"
