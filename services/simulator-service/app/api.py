from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from .world import World


class ScenarioRequest(BaseModel):
    name: str
    duration_s: int = 120


def build_api(world: World) -> FastAPI:
    app = FastAPI(title="GrowthPilot Simulator API", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/world")
    def world_state():
        return world.snapshot()

    @app.post("/scenario/apply")
    def apply_scenario(req: ScenarioRequest):
        world.apply_scenario(req.name, req.duration_s)
        return {"ok": True, "active": world.snapshot()["active_scenario"]}

    @app.post("/scenario/clear")
    def clear_scenario():
        world.clear_scenario()
        return {"ok": True}

    return app