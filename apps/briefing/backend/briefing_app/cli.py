import argparse
from datetime import datetime, UTC
import json
import time
from urllib.request import Request, urlopen

from briefing_app.settings import get_settings
from studio_identity import service_principal, principal_headers


def main():
    parser = argparse.ArgumentParser(description="Request a Studio market briefing through its running API")
    parser.add_argument("report_type", choices=["daily", "weekly"])
    parser.add_argument("--cutoff", help="ISO timestamp including timezone; defaults to the current time")
    parser.add_argument("--scheduled", action="store_true", help="Require the configured formal publisher; preview machines do not generate scheduled editions")
    args = parser.parse_args()
    settings = get_settings()
    if args.scheduled and settings.edition_role != "publisher":
        parser.error("Scheduled editions run only on the configured publisher")
    base = settings.api_base_url.rstrip("/")
    headers = principal_headers(service_principal("briefing"))
    if args.scheduled:
        with urlopen(Request(base + "/status", headers=headers), timeout=30) as response:
            if json.load(response)["edition_role"] != "publisher":
                parser.error("The target API is a preview environment, not the formal publisher")
    cutoff = datetime.fromisoformat(args.cutoff) if args.cutoff else datetime.now(UTC)
    from briefing_app.contracts import GenerateRequest
    payload = GenerateRequest(report_type=args.report_type, cutoff=cutoff).model_dump(mode="json")
    req = Request(base + "/reports", data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json", **headers})
    with urlopen(req, timeout=30) as response:
        result = json.load(response)
    if args.scheduled:
        deadline = time.monotonic() + 3900
        while result["status"] in {"queued", "running"}:
            if time.monotonic() >= deadline:
                raise SystemExit("Scheduled briefing did not finish within 65 minutes; inspect the retained run.")
            time.sleep(5)
            with urlopen(Request(f"{base}/reports/{result['report_id']}/status", headers=headers), timeout=30) as response:
                result = json.load(response)
        if result["status"] != "completed":
            raise SystemExit(result.get("error") or "Scheduled briefing failed; no report was published.")
    print(json.dumps({key: result[key] for key in ("report_id", "report_type", "report_date", "version", "status")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
