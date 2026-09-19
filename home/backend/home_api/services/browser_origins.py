"""The navigation catalog is the single deployment list of trusted browser apps."""
import json
from urllib.parse import urlsplit


def trusted_browser_origins(settings) -> list[str]:
    cards = json.loads(settings.apps_file.read_text(encoding="utf-8"))
    urls = [settings.frontend_url, *settings.cors_origins]
    urls.extend(settings.app_urls.get(card["app_id"], card["url"]) for card in cards)
    origins = set()
    for value in urls:
        if value == "*":
            origins.add(value)
            continue
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username and not parsed.password:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return sorted(origins)
