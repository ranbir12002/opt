# backend/api/personality_api.py
# ──────────────────────────────────────────────────────────────
# GET /api/personality — returns compiled personality blocks.
# Called by the Node.js chat server at startup and cached.
# ──────────────────────────────────────────────────────────────
from fastapi import APIRouter

from personality import get_personality_response

router = APIRouter(tags=["personality"])


@router.get("/personality")
async def get_personality():
    """Return compiled personality blocks for all contexts."""
    return get_personality_response()
