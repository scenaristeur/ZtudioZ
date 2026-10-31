"""Providers LLM : protocol commun, mock deterministe, adaptateur OpenAI-compatible."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    """Contrat minimal d'un provider : estimation de cout et completion."""

    def estimate(self) -> float:
        """Cout estime d'un appel, en credits."""

    async def complete(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        schema: dict | None = None,
    ) -> dict:
        """Produit une sortie structuree pour un agent."""


Responder = dict[str, Any] | Callable[..., dict]


class MockProvider:
    """Provider deterministe : aucun reseau, cout nul par defaut."""

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        delay: float = 0.0,
        cost: float = 0.0,
        default: Any = None,
    ):
        self.responses = dict(responses or {})
        self.delay = delay
        self.cost = cost
        self.default = default
        self.calls = 0
        self.max_concurrency = 0
        self.records: list[dict] = []
        self._active = 0

    def estimate(self) -> float:
        """Cout fixe par appel."""
        return self.cost

    async def complete(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        schema: dict | None = None,
    ) -> dict:
        """Retourne la reponse pre-enregistree pour cet agent."""
        self._active += 1
        self.max_concurrency = max(self.max_concurrency, self._active)
        self.calls += 1
        self.records.append(
            {"agent": agent_id, "model": model, "system": system, "prompt": prompt}
        )
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self._payload(agent_id, prompt, schema)
        finally:
            self._active -= 1

    def _payload(self, agent_id: str, prompt: str, schema: dict | None) -> dict:
        short = agent_id.rsplit("/", 1)[-1]
        for key in (agent_id, short):
            if key in self.responses:
                value = self.responses[key]
                return value(prompt) if callable(value) else value
        if self.default is not None:
            return self.default(prompt) if callable(self.default) else self.default
        # Fallback intelligent : si un schema de sortie est declare,
        # on produit des valeurs factices conformes a la structure attendue.
        if schema:
            out: dict[str, Any] = {}
            for name, spec in schema.get("properties", {}).items():
                if spec.get("type") == "string":
                    out[name] = prompt[:80]
                elif spec.get("type") == "array":
                    out[name] = [prompt[:40]]
                elif spec.get("type") == "object":
                    out[name] = {}
            if out:
                return out
        return {"text": prompt[:160]}


class OpenAICompatProvider:
    """Adaptateur minimal pour toute API compatible OpenAI (agnes, albert...)."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key_env: str = "OPENAI_API_KEY",
        temperature: float = 0.2,
        cost: float = 0.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.temperature = temperature
        self.cost = cost

    def estimate(self) -> float:
        """Cout declare par appel ; affinable quand la table de prix existe."""
        return self.cost

    async def complete(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        schema: dict | None = None,
    ) -> dict:
        """Appelle l'API et retourne le JSON parse, valide par le noyau."""
        import json
        import urllib.request

        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"cle absente: {self.api_key_env}")

        body: dict[str, Any] = {
            "model": model or self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if schema is not None:
            body["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)


__all__ = ["Provider", "MockProvider", "OpenAICompatProvider"]
