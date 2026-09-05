from pydantic import BaseModel


class StudioAppCard(BaseModel):
    app_id: str
    name: str
    url: str
    eyebrow: str
    description: str


class StudioAppsResponse(BaseModel):
    product_name: str
    apps: list[StudioAppCard]
