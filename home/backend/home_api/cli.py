"""Explicit identity initialization and local operator recovery.

Never automatically maps historical business authors or portfolio managers.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import getpass
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_api.core.settings import get_settings
from home_api.db.models import Membership, ServiceCredential, Team, User, now
from home_api.db.session import database_engine, initialize_schema
from home_api.services.auth import hash_password, new_token, read_private_text, token_hash
from home_api.services.identity import AUDIENCES, audit, lock_user, revoke_one_time_tokens, revoke_sessions


def bootstrap(db: Session, *, username: str, display_name: str, team_name: str, password_hash: str) -> dict:
    if db.scalar(select(Team)) or db.scalar(select(User)):
        raise ValueError("Identity already contains users or a team; bootstrap will not replace them.")
    if not password_hash.startswith("scrypt$"):
        raise ValueError("Expected an existing scrypt password hash.")
    user = User(username=username.strip().casefold(), display_name=display_name.strip(), password_hash=password_hash)
    db.add(user)
    db.flush()
    team = Team(id="default", name=team_name, owner_user_id=user.id)
    db.add(team)
    db.flush()
    db.add(Membership(team_id=team.id, user_id=user.id, role="admin"))
    audit(db, "identity_bootstrapped", user.id, team.id)
    db.commit()
    return {"user_id": user.id, "team_id": team.id, "username": user.username}


def write_secret(path: str, content: str) -> None:
    descriptor = os.open(Path(path).expanduser(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Investment Studio identity operator commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Upgrade the versioned identity schema without importing users")
    bootstrap_parser = subparsers.add_parser("bootstrap", help="Create the first owner; business data ownership is a separate explicit migration")
    bootstrap_parser.add_argument("--username", required=True)
    bootstrap_parser.add_argument("--display-name", required=True)
    bootstrap_parser.add_argument("--team-name", default="投资研究团队")
    bootstrap_parser.add_argument("--password-hash-file", help="Explicitly import the previous private scrypt hash file; otherwise prompt for a new password")
    service_parser = subparsers.add_parser("create-service")
    service_parser.add_argument("--service-id", required=True)
    service_parser.add_argument("--display-name", required=True)
    service_parser.add_argument("--audience", action="append", required=True, choices=sorted(AUDIENCES))
    service_parser.add_argument("--scope", action="append", required=True)
    service_parser.add_argument("--expires-days", type=int, default=90)
    service_parser.add_argument("--token-file", required=True, help="New private file; token is never printed")
    revoke_parser = subparsers.add_parser("revoke-service")
    revoke_parser.add_argument("--credential-id", required=True)
    mfa_key = subparsers.add_parser("create-mfa-key")
    mfa_key.add_argument("--output", required=True)
    recovery = subparsers.add_parser("recover-user", help="Local operator recovery revokes all sessions; use only after verifying the user's identity")
    recovery.add_argument("--user-id", required=True)
    recovery.add_argument("--reset-mfa", action="store_true")
    recovery.add_argument("--reset-password", action="store_true")
    args = parser.parse_args()
    if args.command == "create-mfa-key":
        write_secret(args.output, Fernet.generate_key().decode())
        print("MFA encryption key written to the requested private file.")
        return
    database_url = get_settings().database_url
    if args.command == "migrate":
        initialize_schema(database_url)
        print("Identity migrations are current. No users or business ownership were changed.")
        return
    with Session(database_engine(database_url)) as db:
        if args.command == "bootstrap":
            if args.password_hash_file:
                encoded = read_private_text(Path(args.password_hash_file), "Existing password hash")
            else:
                password = getpass.getpass("New owner password (at least 12 characters): ")
                if len(password) < 12 or password != getpass.getpass("Confirm password: "):
                    raise SystemExit("Password too short or confirmation differs.")
                encoded = hash_password(password)
            print(json.dumps(bootstrap(db, username=args.username, display_name=args.display_name, team_name=args.team_name, password_hash=encoded), ensure_ascii=False))
        elif args.command == "create-service":
            team = db.scalar(select(Team))
            if not team or not 1 <= args.expires_days <= 365:
                raise SystemExit("Bootstrap the team first and choose an expiry between 1 and 365 days.")
            token = new_token()
            credential = ServiceCredential(service_id=args.service_id, display_name=args.display_name, team_id=team.id,
                                           audiences=list(dict.fromkeys(args.audience)), scopes=list(dict.fromkeys(args.scope)),
                                           token_hash=token_hash(token), expires_at=now() + timedelta(days=args.expires_days))
            db.add(credential)
            db.flush()
            write_secret(args.token_file, token)
            audit(db, "service_created", target=credential.id, service_id=args.service_id, scopes=credential.scopes)
            db.commit()
            print(json.dumps({"credential_id": credential.id, "service_id": credential.service_id, "token_file": args.token_file}))
        elif args.command == "revoke-service":
            credential = db.get(ServiceCredential, args.credential_id)
            if not credential:
                raise SystemExit("Unknown credential ID.")
            credential.revoked_at = now()
            audit(db, "service_revoked", target=credential.id)
            db.commit()
        elif args.command == "recover-user":
            if not db.get(User, args.user_id) or not (args.reset_mfa or args.reset_password):
                raise SystemExit("Specify an existing user and an explicit recovery action.")
            encoded_password = None
            if args.reset_password:
                password = getpass.getpass("New password (at least 12 characters): ")
                if len(password) < 12 or password != getpass.getpass("Confirm password: "):
                    raise SystemExit("Password too short or confirmation differs.")
                encoded_password = hash_password(password)
            # Complete operator input before taking the account lock.
            user = lock_user(db, args.user_id)
            if not user:
                raise SystemExit("Unknown user ID.")
            if encoded_password is not None:
                user.password_hash = encoded_password
            if args.reset_mfa:
                user.totp_secret = user.totp_pending_secret = user.totp_last_step = None
            user.failed_logins = 0
            user.locked_until = None
            revoke_sessions(db, user.id)
            revoke_one_time_tokens(db, user.id)
            audit(db, "operator_account_recovery", target=user.id, reset_mfa=args.reset_mfa, reset_password=args.reset_password)
            db.commit()
            print("Recovery completed and existing sessions revoked. Account status was preserved.")


if __name__ == "__main__":
    main()
