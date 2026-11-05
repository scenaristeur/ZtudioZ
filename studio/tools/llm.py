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
        images: list | None = None,
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
        images: list | None = None,
    ) -> dict:
        """Retourne la reponse pre-enregistree ou genere une reponse par defaut."""
        self._active += 1
        self.max_concurrency = max(self.max_concurrency, self._active)
        self.calls += 1
        self.records.append(
            {
                "agent": agent_id,
                "model": model,
                "system": system,
                "prompt": prompt,
                "images": [
                    {
                        "hash": getattr(image, "hash", ""),
                        "media_type": getattr(image, "media_type", ""),
                    }
                    for image in (images or [])
                ],
            }
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
    def _fake_value(spec: dict, index: int = 0) -> Any:
        tag = f" #{index + 1}" if index else ""
        t = spec.get("type")
        if t == "string":
            return f"valeur générée par le mock{tag}"
        if t == "boolean":
            return True
        if t == "integer":
            return max(index, int(spec.get("minimum", 0) or 0))
        if t == "number":
            return float(max(index, float(spec.get("minimum", 0) or 0)))
        if t == "array":
            # le mockhonore minItems : une liste d'exemple doit rester conforme
            count = max(1, int(spec.get("minItems", 1) or 1))
            items = spec.get("items")
            return [
                MockProvider._fake_value(items, position) if isinstance(items, dict)
                else f"élément généré #{position + 1}"
                for position in range(count)
            ]
        if t == "object":
            # Si c'est un objet avec un $ref ou properties, generer recursivement.
            if "$ref" in spec:
                return {}
            props = spec.get("properties", {})
            if props:
                return {name: MockProvider._fake_value(sub, index) for name, sub in props.items()}
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

    def _resolve_model(self, agent_model: str) -> str:
        """Un agent qui declare un modele reel garde son modele ; les placeholders
        mock/* cèdent la place au modele configure par l'operateur du run."""
        if agent_model and not agent_model.startswith("mock"):
            return agent_model
        return self.model

    async def complete(
        self,
        *,
        agent_id: str,
        model: str,
        system: str,
        prompt: str,
        schema: dict | None = None,
        output_key: str | None = None,
        images: list | None = None,
    ) -> dict:
        """Appelle l'API et retourne le JSON parse, valide par le noyau."""
        import json
        import urllib.request

        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"cle absente: {self.api_key_env}")

        body: dict[str, Any] = {
            "model": self._resolve_model(model),
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": self._message_content(prompt, images)},
            ],
        }
        # Les modeles vision d'Albert n'exposent pas forcement le mode JSON.
        if schema is not None and not images:
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
        return self._parse_json(content)

    @staticmethod
    def _message_content(prompt: str, images: list | None = None):
        """Contenu texte, ou contenu multimodal si des images sont fournies."""
        if not images:
            return prompt
        import base64

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            media_type = getattr(image, "media_type", "image/png")
            data = getattr(image, "data", b"")
            encoded = base64.b64encode(data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                }
            )
        return content

    @staticmethod
    def _parse_json(content: str) -> dict:
        """JSON strict, avec repli sur le premier objet d'une reponse enrobee."""
        import json

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise


__all__ = ["Provider", "MockProvider", "OpenAICompatProvider"]
