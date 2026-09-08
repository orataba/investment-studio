#!/usr/bin/env python3
"""Explicit, dry-run-first mapping from the former local research identity."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for relative in ("home/backend", "apps/watchlist/backend", "shared-data/instruments/python", "packages/identity"):
    sys.path.insert(0, str(ROOT / relative))

from sqlalchemy import text
from sqlalchemy.orm import Session
from home_api.core.settings import get_settings as identity_settings
from home_api.db.session import database_engine
from home_api.db.models import Membership, User
from home_api.services.identity import audit
from validate_migration_targets import _target_from_url
from watchlist_app.services.research_identity_migration import claim_research_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, help="Real Home user ID; never inferred from the current operator")
    parser.add_argument("--from-user-id", default="local-investor", help="Exact former author ID to map")
    parser.add_argument("--claim-unassigned-conversations", action="store_true", help="Explicitly claim previously unassigned private chats")
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed mapping; omitted means dry-run")
    args = parser.parse_args()
    identity_url = os.environ.get("INVESTMENT_STUDIO_HOME_DATABASE_URL", "")
    watchlist_url = os.environ.get("INVESTMENT_STUDIO_WATCHLIST_DATABASE_URL", "")
    if not identity_url or not watchlist_url:
        raise SystemExit("Set both identity and Watchlist database URLs explicitly before mapping authors.")
    if _target_from_url("HOME", identity_url) != _target_from_url("WATCHLIST", watchlist_url):
        raise SystemExit("Identity and research must belong to the same deployment database.")
    with Session(database_engine(identity_settings().database_url)) as session:
        # Identity tables are schema-qualified; research and its audit commit together.
        session.execute(text("SET LOCAL search_path TO watchlist, public"))
        user = session.get(User, args.user_id)
        member = session.get(Membership, ("default", args.user_id))
        if not user or not user.active or not user.password_hash or not member:
            raise SystemExit("Destination must be an activated member of the default team.")
        result = claim_research_identity(session, user_id=user.id, display_name=user.display_name,
            team_id=member.team_id, from_user_id=args.from_user_id,
            claim_unassigned_conversations=args.claim_unassigned_conversations, dry_run=not args.apply)
        if args.apply:
            audit(session, "legacy_research_identity_claimed", target=user.id,
                  source_user_id=args.from_user_id, claim_unassigned_conversations=args.claim_unassigned_conversations,
                  counts=result, operator="local_database_operator")
            session.commit()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
