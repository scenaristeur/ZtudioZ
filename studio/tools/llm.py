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
        output_key: str | None = None,
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
        output_key: str | None = None,
    ) -> dict:
        """Retourne la reponse pre-enregistree ou genere une reponse par defaut."""
        self._active += 1
        self.max_concurrency = max(self.max_concurrency, self._active)
        self.calls += 1
        self.records.append(
            {"agent": agent_id, "model": model, "system": system, "prompt": prompt}
        )
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self._payload(agent_id, prompt, schema, output_key)
        finally:
            self._active -= 1

    def _payload(self, agent_id: str, prompt: str, schema: dict | None, output_key: str | None) -> dict:
        short = agent_id.rsplit("/", 1)[-1]
        for key in (agent_id, short):
            if key in self.responses:
                value = self.responses[key]
                return value(prompt) if callable(value) else value
        if self.default is not None:
            return self.default(prompt) if callable(self.default) else self.default
        # Fallback intelligent : on produit un objet conforme au schema.
        if schema:
            props = schema.get("properties", {})
            if props:
                # Le schema contient les proprietes de sortie : on les genere
                # directement. Si un output_key est declare, on enveloppe.
                value = {name: self._fake_value(spec) for name, spec in props.items()}
                if output_key:
                    return {output_key: value}
                return value
            value = self._fake_value(schema)
            if output_key:
                return {output_key: value}
            return value
        return {"text": prompt[:160]}

    @staticmethod
    def _fake_value(spec: dict) -> Any:
        t = spec.get("type")
        if t == "string":
            return "valeur generée par le mock"
        if t == "array":
            return ["élément généré"]
        if t == "object":
            # Si c'est un objet avec un $ref ou properties, generer recursivement.
            if "$ref" in spec:
                return {}
            props = spec.get("properties", {})
            if props:
                return {name: MockProvider._fake_value(sub) for name, sub in props.items()}
            return {}
        return ""


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
        output_key: str | None = None,
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
