import json

from fastapi import APIRouter
from pydantic import TypeAdapter

from home_api.api.contracts import StudioAppCard, StudioAppsResponse
from home_api.core.settings import get_settings


router = APIRouter()


@router.get("", response_model=StudioAppsResponse)
def list_apps() -> StudioAppsResponse:
    settings = get_settings()
    cards = TypeAdapter(list[StudioAppCard]).validate_python(
        json.loads(settings.apps_file.read_text(encoding="utf-8"))
    )
    for card in cards:
        card.url = settings.app_urls.get(card.app_id, card.url)
    return StudioAppsResponse(product_name="Investment Studio", apps=cards)
