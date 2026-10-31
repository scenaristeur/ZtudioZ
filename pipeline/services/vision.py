from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI
from PIL import Image

logger = logging.getLogger(__name__)


class VisionAnalysisService:
    """Analyse d'images via vision LLM (Albert API / OpenAI-compatible).

    Utilise les credentials du .env (OPENAI_API_KEY, OPENAI_BASE_URL)
    et le modèle configuré (MODEL_LARGE = gemma-4-31b-it vision).
    """

    MAX_PIXELS = 1024 * 768

    def __init__(
        self,
        model: Optional[str] = None,
        max_pixels: Optional[int] = None,
        client_timeout: int = 120,
    ):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY manquant pour VisionAnalysisService "
                "(configurer la variable dans .env)"
            )
        self.base_url = os.environ.get(
            "OPENAI_BASE_URL", "https://albert.api.etalab.gouv.fr/v1"
        )
        self.model = model or os.environ.get("MODEL_LARGE", "gemma-4-31b-it")
        self.max_pixels = max_pixels or self.MAX_PIXELS
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=client_timeout,
        )

    def _resize_image(self, data: bytes) -> bytes:
        """Réduit une image si elle dépasse max_pixels, pour éviter les timeouts API."""
        buf = io.BytesIO(data)
        img = Image.open(buf)
        w, h = img.size
        current_pixels = w * h
        if current_pixels <= self.max_pixels:
            return data
        ratio = (self.max_pixels / current_pixels) ** 0.5
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        out = io.BytesIO()
        fmt = img.format or "PNG"
        img.save(out, format=fmt, optimize=True)
        return out.getvalue()

    def _resolve_image(self, path_or_url: str) -> str:
        """Convertit un chemin local en data URI, ou retourne l'URL telle quelle."""
        if path_or_url.startswith(("http://", "https://", "data:")):
            return path_or_url
        p = Path(path_or_url)
        if p.exists():
            mimetype = "image/png"
            if p.suffix in (".jpg", ".jpeg"):
                mimetype = "image/jpeg"
            elif p.suffix == ".webp":
                mimetype = "image/webp"
            data = p.read_bytes()
            data = self._resize_image(data)
            b64 = base64.b64encode(data).decode("utf-8")
            return f"data:{mimetype};base64,{b64}"
        return path_or_url

    def analyze(
        self,
        prompt: str,
        images: list[str],
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> str:
        """Envoie une requête vision à l'API Albert.

        Args:
            prompt: Description / question sur les images
            images: URLs ou chemins locaux
            temperature: Créativité (0-1)
            max_tokens: Longueur max réponse

        Returns:
            Texte de l'analyse
        """
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt}
        ]
        for img in images:
            if img:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": self._resolve_image(img)},
                })

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    def analyze_character_sheet(
        self,
        character_name: str,
        images: list[str],
    ) -> str:
        """Analyse une fiche personnage : apparence, style, cohérence."""
        prompt = (
            f"Tu analyses la fiche personnage de '{character_name}' pour un film.\n"
            f"Décris précisément :\n"
            f"1. Apparence physique (âge, silhouette, traits)\n"
            f"2. Style vestimentaire et couleurs\n"
            f"3. Posture et attitude\n"
            f"4. Accessoires distinctifs\n"
            f"5. Ambiance visuelle (éclairage, palette)\n"
            f"Sois précis, en français."
        )
        return self.analyze(prompt, images)

    def analyze_animatic(
        self,
        scene_label: str,
        images: list[str],
    ) -> str:
        """Analyse un storyboard/animatique : composition, séquences, style."""
        prompt = (
            f"Tu analyses un storyboard/animatique pour la {scene_label} d'un film.\n"
            f"Décris précisément :\n"
            f"1. Nombre de cases et composition de la planche\n"
            f"2. Actions et mouvements de caméra dans chaque case\n"
            f"3. Style visuel (noir et blanc, sépia, couleurs, contrastes)\n"
            f"4. Personnages présents\n"
            f"5. Ambiance et intention dramatique\n"
            f"Sois précis, réponds en français."
        )
        return self.analyze(prompt, images)

    def compare_images(
        self,
        label: str,
        images: list[str],
    ) -> str:
        """Compare plusieurs images pour vérifier la cohérence."""
        prompt = (
            f"Tu compares les images de '{label}' ci-dessous.\n"
            f"1. Les éléments sont-ils cohérents entre les images ?\n"
            f"2. Y a-t-il des différences notables ?\n"
            f"3. Note globale de cohérence : bonne / moyenne / mauvaise\n"
            f"Réponds en français, sois précis."
        )
        return self.analyze(prompt, images)
