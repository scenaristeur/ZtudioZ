from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from pipeline.config import get_service_config
from pipeline.consumption import get_tracker
from pipeline.utils import upload_to_tmpfiles

logger = logging.getLogger(__name__)


class ImageService:
    def __init__(self, config: dict | None = None):
        self.config = config or get_service_config("image")

    def _should_fallback(self, exc: Exception) -> bool:
        """Un échec du provider actif déclenche-t-il un fallback ?

        True si 402 (crédits), 403/429/500/502/503/504 (serveur), ou erreur réseau.
        Pas de fallback pour une erreur 4xx non-éligible (ex. 400 = prompt invalide → le
        backup échouerait aussi).
        """
        import requests as _r
        if isinstance(exc, _r.HTTPError):
            code = exc.response.status_code if exc.response is not None else 0
            return code in (402, 403, 429, 500, 502, 503, 504)
        return isinstance(exc, _r.RequestException)

    def generate(
        self,
        prompt: str,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        model: str | None = None,
        ref_image: str | None = None,
        output_dir: str | Path | None = None,
        filename: str | None = None,
    ) -> str:
        return self._generate_inner(
            prompt=prompt, seed=seed, width=width, height=height, model=model,
            ref_image=ref_image, output_dir=output_dir, filename=filename, attempted=frozenset(),
        )

    def _generate_inner(
        self, prompt, seed, width, height, model, ref_image, output_dir, filename, attempted,
    ) -> str:
        cfg = self.config
        w = width or cfg.get("width", 256)
        h = height or cfg.get("height", 256)
        s = seed if seed is not None else cfg.get("seed")
        m = model or cfg.get("model", "flux")

        active_provider = cfg.get("type", "api")

        try:
            if active_provider == "gradio":
                return self._generate_focus(prompt, s, w, h, m, output_dir, filename)
            if active_provider == "agnes":
                if ref_image and "|" in ref_image:
                    return self.compose(
                        prompt, ref_images=[r for r in ref_image.split("|") if r],
                        width=w, height=h, output_dir=output_dir, filename=filename,
                    )
                return self._generate_agnes(prompt, w, h, m, output_dir, filename, ref_image=ref_image)
            return self._generate_pollinations(prompt, s, w, h, m, output_dir, filename, ref_image=ref_image)
        except Exception as exc:
            return self._fallback_generate(
                prompt, seed, width, height, model, ref_image, output_dir, filename, exc, attempted,
            )

    def _backup_services(self, provider_name: str) -> list["ImageService"]:
        """Instances backups du service (même type que self), pour le fallback provider.

        Lit les providers de config.yaml ordonnés [actif, ...backups] et renvoie les
        instances backup. Ignore les erreurs de résolution (¬AGENTS_API_KEY, etc.) pour ne
        pas casser le provider principal.
        """
        try:
            from pipeline.config import get_service_providers_ordered
            ordered = get_service_providers_ordered("image")
        except Exception:
            return []
        if not ordered:
            return []
        backups = []
        for name, pconf in ordered:
            if name == provider_name:
                continue
            try:
                backups.append(ImageService(config=pconf))
            except Exception:
                continue
        return backups

    def _should_fallback(self, exc: Exception) -> bool:
        """Un échec du provider actif déclenche-t-il un fallback ?

        True si 402 (crédits), 403/429/500/502/503/504 (serveur), ou erreur réseau.
        Pas de fallback pour une erreur 4xx non-éligible (ex. 400 = prompt invalide → le
        backup échouerait aussi).
        """
        import requests as _r
        if isinstance(exc, _r.HTTPError):
            code = exc.response.status_code if exc.response is not None else 0
            return code in (402, 403, 429, 500, 502, 503, 504)
        return isinstance(exc, _r.RequestException)

    def _fallback_generate(self, prompt, seed, width, height, model, ref_image, output_dir, filename, exc, attempted):
        """Retente la génération sur un provider backup (fallback automatique).

        Post du clip V4 : quand le provider actif (Pollinations) renvoie 402/503, la
        production peut s'arrêter entièrement faute de bascule automatique. On retente ici
        sur chaque provider backup de config.yaml (ex. Agnes local) en ordre, sans revisiter
        un provider déjà tenté (set `attempted` propagé → garantit la terminaison).
        """
        if not self._should_fallback(exc):
            raise exc
        provider_name = self.config.get("name", self.config.get("type", "api"))
        logger.warning("Image provider actif en échec (%s) → fallback (déjà tentés: %s)",
                       exc, sorted(attempted | {provider_name}))
        last = exc
        for backup in self._backup_services(provider_name):
            bname = backup.config.get("name", backup.config.get("type", "api"))
            if bname in attempted:
                continue
            new_attempted = attempted | {provider_name, bname}
            try:
                return backup._generate_inner(
                    prompt=prompt, seed=seed, width=width, height=height, model=model,
                    ref_image=ref_image, output_dir=output_dir, filename=filename,
                    attempted=new_attempted,
                )
            except Exception as bexc:
                last = bexc
                if not self._should_fallback(bexc):
                    raise bexc
        raise last

    _AGNES_VALID_SIZES = {
        "1024x1024", "768x1152", "1152x768", "768x1344",
        "1344x768", "1792x1024", "1024x1792",
    }

    def _nearest_agnes_size(self, width: int, height: int) -> str:
        valid = self.config.get("valid_sizes") or list(self._AGNES_VALID_SIZES)

        def area(s: str) -> int:
            a, b = s.split("x")
            return int(a) * int(b)

        target = width * height
        candidates = sorted(valid, key=lambda s: (abs(area(s) - target), area(s)))
        return candidates[0]

    def _generate_agnes(
        self, prompt: str, width: int, height: int, model: str,
        output_dir: str | Path | None, filename: str | None,
        ref_image: str | None = None,
    ) -> str:
        base_url = self.config.get("base_url", "http://localhost:8765").rstrip("/")
        size = self.config.get("size") or self._nearest_agnes_size(width, height)
        if self.config.get("dev"):
            size = self.config.get("dev_size") or size

        # Signature de contenu de la ref : une ref régénérée au même chemin ne doit pas
        # servir l'ancienne image depuis le cache.
        from pipeline.services.refsig import ref_signature
        ref_sig = ref_signature(ref_image or "")

        cache_key = hashlib.md5(f"agnes:{prompt}{size}{model}{ref_image}{ref_sig}".encode()).hexdigest()
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            fname = filename or f"{cache_key}.png"
            out_path = out / fname
            if out_path.exists():
                logger.info(f"Cache hit: {out_path}")
                get_tracker().track("image", "agnes", "cache_hit", duration_ms=0)
                return str(out_path)

        data: dict[str, str] = {"prompt": prompt, "size": size}
        files: dict[str, tuple] = {}
        if ref_image:
            if ref_image.startswith(("http://", "https://")):
                ref_image = upload_to_tmpfiles(ref_image)
            ref_path = Path(ref_image)
            files["reference_image"] = (ref_path.name, ref_path.open("rb"), "image/png")

        if model and model not in (None, "gptimage", "default"):
            logger.info(
                f"Modèle local Agnes : paramètre 'model={model}' ignoré "
                f"(le serveur :8765 fixe son propre modèle)"
            )

        logger.info(f"Generating image via Agnes: {size} model={model}")
        t0 = time.time()
        try:
            resp = requests.post(f"{base_url}/api/image/generate", data=data, files=files, timeout=180)
            resp.raise_for_status()
            task_id = resp.json().get("task_id")
            if not task_id:
                raise RuntimeError(f"Agnes image gen returned no task_id: {resp.text[:200]}")

            img_resp = requests.get(f"{base_url}/api/image/{task_id}", timeout=180)
            img_resp.raise_for_status()
            elapsed = (time.time() - t0) * 1000
            get_tracker().track("image", "agnes", "generate", duration_ms=elapsed)
            if output_dir:
                out_path.write_bytes(img_resp.content)
                return str(out_path)
            return f"{base_url}/api/image/{task_id}"
        except requests.RequestException as e:
            elapsed = (time.time() - t0) * 1000
            get_tracker().track("image", "agnes", "generate", duration_ms=elapsed, status="error", error=str(e))
            raise RuntimeError(f"Agnes image gen failed: {e}")

    def compose(
        self,
        prompt: str,
        ref_images: list[str],
        width: int = 1024,
        height: int = 768,
        output_dir: str | Path | None = None,
        filename: str | None = None,
        size_tier: str | None = None,
    ) -> str:
        """Composer plusieurs images de référence en une seule via agnes-image-2.1-flash.

        Args:
            prompt: Instruction décrivant comment combiner les références
            ref_images: Chemins locaux ou URLs des images de référence
            width, height: Dimensions de sortie
            output_dir: Dossier de sortie (optionnel)
            filename: Nom du fichier (optionnel)
            size_tier: Tier de résolution (1K/2K/3K/4K). En dev, 1K par défaut.

        Returns:
            str: Chemin de l'image composée
        """
        apihub_url = self.config.get("apihub_url", "https://apihub.agnes-ai.com").rstrip("/")
        api_key = self.config.get("api_key", "") or os.environ.get("AGNES_API_KEY", "")
        model = self.config.get("compose_model", "agnes-image-2.1-flash")

        if not api_key:
            raise RuntimeError("AGNES_API_KEY manquant pour la composition d'images")

        if self.config.get("dev") and not size_tier:
            size_tier = "1K"
            logger.info("Dev mode: composition en 1K (basse résolution)")
        _RATIOS = ["1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"]
        if size_tier:
            payload_size = size_tier
            ratio_value = width / height if height else 1.0
            payload_ratio = min(_RATIOS, key=lambda r: abs(eval(r.replace(":", "/")) - ratio_value))
        else:
            payload_size = f"{width}x{height}"
            payload_ratio = None

        cache_key = hashlib.md5(
            f"agnes:compose:{prompt}{payload_size}{payload_ratio}{ref_images}".encode()
        ).hexdigest()
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            fname = filename or f"{cache_key}.png"
            out_path = out / fname
            if out_path.exists():
                logger.info(f"Cache hit: {out_path}")
                return str(out_path)

        images: list[str] = []
        for ref in ref_images:
            if ref.startswith(("http://", "https://")):
                images.append(ref)
            else:
                path = Path(ref)
                if not path.exists():
                    raise RuntimeError(f"Image de référence introuvable: {ref}")
                b64 = base64.b64encode(path.read_bytes()).decode()
                images.append(f"data:image/png;base64,{b64}")

        payload = {
            "model": model,
            "prompt": prompt,
            "size": payload_size,
            "extra_body": {
                "image": images,
                "response_format": "url",
            },
        }
        if payload_ratio:
            payload["ratio"] = payload_ratio

        logger.info(f"Composing {len(images)} refs via {model} {payload_size}")
        t0 = time.time()
        try:
            resp = requests.post(
                f"{apihub_url}/v1/images/generations",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=360,
            )
            resp.raise_for_status()
            data = resp.json()
            url = data.get("data", [{}])[0].get("url")
            if not url:
                raise RuntimeError(f"Agnes compose returned no URL: {resp.text[:300]}")
            img_resp = requests.get(url, timeout=180)
            img_resp.raise_for_status()
            elapsed = (time.time() - t0) * 1000
            get_tracker().track("image", model, "compose", duration_ms=elapsed)
            if output_dir:
                out_path.write_bytes(img_resp.content)
                return str(out_path)
            return url
        except requests.RequestException as e:
            raise RuntimeError(f"Agnes compose failed: {e}")

    def _generate_pollinations(
        self, prompt: str, seed: int | None, width: int, height: int,
        model: str, output_dir: str | Path | None, filename: str | None,
        ref_image: str | None = None,
    ) -> str:
        import time as _time
        t0 = _time.time()
        api_key = self.config.get("api_key", "")
        if not api_key:
            try:
                env_path = Path(__file__).resolve().parent.parent.parent / ".env"
                if env_path.exists():
                    for line in env_path.read_text().splitlines():
                        if line.startswith("POLLINATION_API_KEY="):
                            api_key = line.split("=", 1)[1].strip()
                            break
            except Exception:
                pass
        encoded = quote(prompt)
        url = f"https://gen.pollinations.ai/image/{encoded}"
        params = {"model": model, "nologo": "true"}
        if width:
            params["width"] = width
        if height:
            params["height"] = height
        if seed is not None:
            params["seed"] = seed
        if api_key:
            params["key"] = api_key
        if ref_image:
            if ref_image.startswith(("http://", "https://")):
                params["image"] = ref_image
            else:
                params["image"] = upload_to_tmpfiles(ref_image)

        from pipeline.services.refsig import ref_signature
        ref_sig = ref_signature(ref_image or "")

        cache_key = hashlib.md5(f"{prompt}{seed}{width}{height}{model}{ref_image}{ref_sig}".encode()).hexdigest()
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            ext = ".png"
            fname = filename or f"{cache_key}{ext}"
            out_path = out / fname
            if out_path.exists():
                logger.info(f"Cache hit: {out_path}")
                get_tracker().track("image", model, "cache_hit", duration_ms=0)
                return str(out_path)

        # Multiref : la route GET ne supporte pas le param `image` répété (400).
        # Passer par la route JSON /v1/images/generations avec `image` en LISTE,
        # seule façon de faire réellement parvenir plusieurs refs au modèle.
        refs_list = [r for r in (ref_image or "").split("|") if r]
        if len(refs_list) > 1:
            return self._generate_pollinations_multiref(
                prompt, width, height, model, refs_list, api_key, output_dir, filename
            )

        ref_support = {"gpt-image-2": True, "gptimage": True, "gptimage-large": True, "flux": False, "sana": False}
        fallback_models = {"gpt-image-2": ["gptimage", "flux"], "gptimage": ["flux"], "flux": None}
        strict_refs = self.config.get("strict_refs", False)
        no_fallback = self.config.get("no_model_fallback", False)
        if strict_refs and ref_image:
            fallback_models = {k: [] for k in fallback_models}
        if no_fallback:
            fallback_models = {k: [] for k in fallback_models}
        models_to_try = [model]
        if model in fallback_models and fallback_models[model]:
            models_to_try.extend(fallback_models[model])

        last_error = ""
        for current_model in models_to_try:
            params["model"] = current_model
            if not ref_support.get(current_model, True) and "image" in params:
                if strict_refs:
                    raise RuntimeError(
                        f"strict_refs: le modèle {current_model} ne supporte pas les refs images "
                        f"(configurer strict_refs: false pour autoriser le fallback sans refs)"
                    )
                logger.info(f"Model {current_model} doesn't support ref images, removing ref")
                del params["image"]
            logger.info(f"Generating image: {current_model} {width}x{height} seed={seed}")

            for attempt in range(2):
                try:
                    t_start = _time.time()
                    resp = requests.get(url, params=params, timeout=180)
                    if resp.status_code == 429:
                        _time.sleep(4 * (2 ** attempt))
                        continue
                    if resp.status_code == 500:
                        last_error = f"Model {current_model} returned 500"
                        logger.warning(last_error)
                        break  # Try next model
                    resp.raise_for_status()
                    elapsed = (_time.time() - t_start) * 1000
                    get_tracker().track("image", current_model, "generate", duration_ms=elapsed)
                    if output_dir:
                        out_path.write_bytes(resp.content)
                        return str(out_path)
                    return resp.url
                except requests.RequestException as e:
                    last_error = str(e)
                    logger.warning(f"Attempt {attempt+1}/{current_model} failed: {e}")
                    _time.sleep(2 ** attempt)

        elapsed = (_time.time() - t0) * 1000
        get_tracker().track("image", model, "generate", duration_ms=elapsed, status="error", error=last_error)
        raise RuntimeError(f"Image gen failed after fallback: {last_error}")

    def _generate_pollinations_multiref(
        self, prompt: str, width: int | None, height: int | None,
        model: str, refs: list[str], api_key: str,
        output_dir: str | Path | None, filename: str | None,
    ) -> str:
        """Génération pollinations avec plusieurs images de référence.

        La route GET `gen.pollinations.ai/image/{prompt}` refuse un param `image`
        répété (400). La route JSON `/v1/images/generations` accepte `image` en LISTE,
        seule forme qui fasse réellement parvenir toutes les refs au modèle.
        Réponse en `b64_json` pour un écriture locale directe.
        """
        size = None
        if width and height:
            size = f"{width}x{height}"
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "image": refs,
            "response_format": "b64_json",
        }
        if size:
            payload["size"] = size
        if api_key:
            payload["user"] = api_key[:24]

        t0 = time.time()
        url = "https://gen.pollinations.ai/v1/images/generations"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            b64 = (data.get("data") or [{}])[0].get("b64_json")
            if not b64:
                raise RuntimeError(f"Pollinations multiref returned no b64_json: {resp.text[:300]}")
            raw = base64.b64decode(b64)
            elapsed = (time.time() - t0) * 1000
            get_tracker().track("image", model, "generate", duration_ms=elapsed)
            if output_dir:
                out = Path(output_dir)
                out.mkdir(parents=True, exist_ok=True)
                fname = filename or f"multiref_{model}_{hashlib.md5(str(refs).encode()).hexdigest()[:8]}.png"
                out_path = out / fname
                out_path.write_bytes(raw)
                return str(out_path)
            return f"data:image/png;base64,{b64}"
        except requests.RequestException as e:
            raise RuntimeError(f"Pollinations multiref failed: {e}")

    def _generate_focus(
        self, prompt: str, seed: int | None, width: int, height: int,
        model: str, output_dir: str | Path | None, filename: str | None,
    ) -> str:
        url = self.config.get("url", "http://localhost:7860")
        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "seed": seed or -1,
        }
        try:
            resp = requests.post(f"{url}/sdapi/v1/txt2img", json=payload, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            import base64
            img_data = base64.b64decode(data["images"][0])
            if output_dir:
                out = Path(output_dir)
                out.mkdir(parents=True, exist_ok=True)
                fname = filename or f"focus_{seed or 'nosd'}.png"
                out_path = out / fname
                out_path.write_bytes(img_data)
                return str(out_path)
            return "generated"
        except requests.RequestException as e:
            raise RuntimeError(f"FOOCUS generation failed: {e}")
