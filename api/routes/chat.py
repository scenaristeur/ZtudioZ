from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ChatMessage(BaseModel):
    message: str


@router.post("/{slug}/chat")
def chat(slug: str, msg: ChatMessage):
    """Concierge chat endpoint. Placeholder for LLM integration."""
    return {
        "response": (
            f"Received your message for project '{slug}': "
            f"'{msg.message}'. "
            "The concierge agent will be implemented in Phase 6 "
            "with the Vue.js frontend."
        ),
        "project": slug,
    }
