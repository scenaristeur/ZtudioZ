from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

from pipeline.config import get_service_config
from pipeline.consumption import get_tracker
from pipeline.utils import upload_to_tmpfiles

logger = logging.getLogger(__name__)


class VideoService:
    def __init__(self, config: dict | None = None):
        self.config = config or get_service_config("video")

    def generate(
        self,
        prompt: str,
        ref_image: str | None = None,
        ref_images: list[str] | None = None,
        seed: int | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
        model: str | None = None,
        output_dir: str | Path | None = None,
        filename: str | None = None,
    ) -> str:
        return self._generate_attempted(
            prompt, ref_image, ref_images, seed, width, height,
            duration, model, output_dir, filename, frozenset(),
        )

    def _generate_attempted(
        self, prompt, ref_image, ref_images, seed, width, height,
        duration, model, output_dir, filename, attempted,
    ) -> str:
        cfg = self.config
        w = width or cfg.get("width", 256)
        h = height or cfg.get("height", 256)
        d = duration or cfg.get("duration", 5)
        s = seed if seed is not None else cfg.get("seed")
        m = model or cfg.get("model", "grok-video-pro")

        refs: list[str] = []
        if ref_image:
            refs = [r for r in ref_image.split("|") if r]
        if ref_images:
            refs = [r for r in ref_images if r]
        refs = list(dict.fromkeys(refs))

        # La clé doit décrire le *contenu* des refs, pas leur chemin : une ref
        # régénérée au même endroit invalide le cache (sinon l'ancienne vidéo est
        # servie silencieusement, annulant la correction).
        from pipeline.services.refsig import refs_signature
        refs_sig = refs_signature(refs)

        t0 = time.time()

        cache_key = hashlib.md5(f"{prompt}{s}{w}{h}{d}{m}{'|'.join(refs)}{refs_sig}".encode()).hexdigest()
        if output_dir:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            fname = filename or f"{cache_key}.mp4"
            out_path = out / fname
            if out_path.exists():
                logger.info(f"Cache hit: {out_path}")
                get_tracker().track("video", m, "cache_hit", duration_ms=0)
                return str(out_path)

        logger.info(f"Generating video: {m} {d}s {w}x{h} seed={s}")
        try:
            return self._dispatch_generate(prompt, w, h, d, s, m, refs, output_dir, out_path, attempted)
        except Exception as exc:
            return self._fallback_generate(
                prompt, refs, seed, width, height, duration, model,
                output_dir, filename, exc, attempted,
            )

    def _dispatch_generate(self, prompt, w, h, d, s, m, refs, output_dir, out_path, attempted) -> str:
        """Branche sur le provider (agnes/pollinations). Séparée pour le fallback."""
        if self.config.get("type") == "agnes":
            if self.config.get("direct"):
                return self._generate_agnes_apihub(prompt, w, h, d, s, output_dir, out_path, ref_images=refs or None)
            return self._generate_agnes(prompt, w, h, d, s, output_dir, out_path, ref_images=refs or None)
        return self._generate_pollinations_video(prompt, w, h, d, s, m, refs, output_dir, out_path)

    def _generate_pollinations_video(self, prompt, w, h, d, s, m, refs, output_dir, out_path) -> str:
        cfg = self.config
        t0 = time.time()
        api_key = cfg.get("api_key", "")

        encoded_prompt = prompt.replace(" ", "%20")

        url = f"https://gen.pollinations.ai/video/{encoded_prompt}"

        params = {
            "width": w,
            "height": h,
            "duration": d,
            "model": m,
        }
        if s is not None:
            params["seed"] = s
        if api_key:
            params["key"] = api_key
        if refs:
            if refs[0].startswith(("http://", "https://")):
                params["image"] = refs[0]
            else:
                params["image"] = upload_to_tmpfiles(refs[0])

        error = ""
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=300)
                if resp.status_code == 429:
                    wait = 4 * (2 ** attempt)
                    logger.warning(f"Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                if "video" in ct or resp.content[:4] in (b"\x00\x00\x00\x20", b"ftyp"):
                    elapsed = (time.time() - t0) * 1000
                    get_tracker().track("video", m, "generate", duration_ms=elapsed)
                    if output_dir:
                        out_path.write_bytes(resp.content)
                        return str(out_path)
                    return resp.url
                text = resp.text[:200]
                error = f"Non-video response: {text}"
                logger.warning(f"Unexpected response: {text}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Video gen returned non-video: {text}")
            except requests.RequestException as e:
                error = str(e)
                logger.warning(f"Attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    elapsed = (time.time() - t0) * 1000
                    get_tracker().track("video", m, "generate", duration_ms=elapsed, status="error", error=error)
                    raise
        elapsed = (time.time() - t0) * 1000
        get_tracker().track("video", m, "generate", duration_ms=elapsed, status="error", error=error)
        raise RuntimeError(f"Failed after 3 attempts: {prompt[:60]}")

    def _backup_services(self, provider_name: str) -> list["VideoService"]:
        """Instances backups du service (fallback provider). Lit config.yaml ordonnée."""
        try:
            from pipeline.config import get_service_providers_ordered
            ordered = get_service_providers_ordered("video")
        except Exception:
            return []
        if not ordered:
            return []
        backups = []
        for name, pconf in ordered:
            if name == provider_name:
                continue
            try:
                backups.append(VideoService(config=pconf))
            except Exception:
                continue
        return backups

    def _should_fallback(self, exc: Exception) -> bool:
        import requests as _r
        if isinstance(exc, _r.HTTPError):
            code = exc.response.status_code if exc.response is not None else 0
            return code in (402, 403, 429, 500, 502, 503, 504)
        return isinstance(exc, _r.RequestException)

    def _fallback_generate(self, prompt, refs, seed, width, height, duration, model, output_dir, filename, exc, attempted) -> str:
        """Retente sur un provider backup (fallback automatique, anti-boucle)."""
        if not self._should_fallback(exc):
            raise exc
        provider_name = self.config.get("name", self.config.get("type", "api"))
        logger.warning("Video provider actif en échec (%s) → fallback (déjà tentés: %s)",
                       exc, sorted(attempted | {provider_name}))
        last = exc
        for backup in self._backup_services(provider_name):
            bname = backup.config.get("name", backup.config.get("type", "api"))
            if bname in attempted:
                continue
            new_attempted = attempted | {provider_name, bname}
            try:
                return backup._generate_attempted(
                    prompt, ref_image=(refs[0] if refs else None),
                    ref_images=refs, seed=seed, width=width, height=height,
                    duration=duration, model=model, output_dir=output_dir, filename=filename,
                    attempted=new_attempted,
                )
            except Exception as bexc:
                last = bexc
                if not self._should_fallback(bexc):
                    raise bexc
        raise last

    _AGNES_VALID_DURATIONS = (5, 10, 15, 18, 20)

    def _generate_agnes(
        self, prompt: str, width: int, height: int, duration: int,
        seed: int | None, output_dir, out_path: Path,
        ref_image: str | None = None,
        ref_images: list[str] | None = None,
    ) -> str:
        base_url = self.config.get("base_url", "http://localhost:8765").rstrip("/")

        if self.config.get("dev"):
            width = self.config.get("dev_width", width)
            height = self.config.get("dev_height", height)
            logger.info(f"Dev mode: vidéo en {width}x{height} (basse résolution)")

        d = min(
            self.config.get("valid_durations") or self._AGNES_VALID_DURATIONS,
            key=lambda x: abs(x - duration),
        )

        refs: list[str] = []
        if ref_image:
            refs = [r for r in ref_image.split("|") if r]
        if ref_images:
            refs = [r for r in ref_images if r]
        refs = list(dict.fromkeys(refs))
        if len(refs) > 2:
            logger.warning(
                f"Le serveur local ne gère que 2 images (start/end) : "
                f"{len(refs)} fournies, les 2 premières sont utilisées"
            )
            refs = refs[:2]

        if len(refs) >= 2:
            mode = "keyframes"
        elif len(refs) == 1:
            mode = "i2v"
        else:
            mode = "t2v"

        data: dict = {
            "prompt": prompt,
            "mode": mode,
            "duration": d,
            "video_width": width,
            "video_height": height,
        }
        if seed is not None:
            data["seed"] = seed

        files: dict[str, tuple] = {}
        if refs:
            start = refs[0]
            if start.startswith(("http://", "https://")):
                start = upload_to_tmpfiles(start)
            start_path = Path(start)
            files["reference_image"] = (start_path.name, start_path.open("rb"), "image/png")
            if len(refs) >= 2:
                end = refs[1]
                if end.startswith(("http://", "https://")):
                    end = upload_to_tmpfiles(end)
                end_path = Path(end)
                files["end_frame_image"] = (end_path.name, end_path.open("rb"), "image/png")

        logger.info(f"Generating video via Agnes: mode={mode} {d}s {width}x{height} seed={seed}")
        try:
            resp = requests.post(f"{base_url}/api/tasks/simple", data=data, files=files, timeout=120)
            resp.raise_for_status()
            task_id = resp.json().get("task_id")
            if not task_id:
                raise RuntimeError(f"Agnes video gen returned no task_id: {resp.text[:200]}")

            task = self._poll_agnes_task(base_url, task_id, out_path)
            return str(out_path) if output_dir else f"{base_url}/api/video/{task_id}"
        except requests.RequestException as e:
            raise RuntimeError(f"Agnes video gen failed: {e}")

    def _poll_agnes_task(self, base_url: str, task_id: str, out_path: Path) -> None:
        import time as _time
        deadline = _time.time() + 3600
        last_status = ""
        while _time.time() < deadline:
            try:
                resp = requests.get(f"{base_url}/api/tasks/{task_id}", timeout=60)
                resp.raise_for_status()
                task = resp.json()
            except requests.RequestException:
                _time.sleep(15)
                continue
            status = task.get("status", "")
            if status != last_status:
                logger.info(f"Agnes task {task_id}: {status} "
                            f"({task.get('current_message') or ''})")
                last_status = status
            if status == "completed":
                video_resp = requests.get(f"{base_url}/api/video/{task_id}", timeout=300)
                video_resp.raise_for_status()
                out_path.write_bytes(video_resp.content)
                return
            if status == "failed":
                raise RuntimeError(f"Agnes task {task_id} failed: {task.get('current_message') or task.get('error') or ''}")
            _time.sleep(10)
        raise RuntimeError(f"Agnes task {task_id} timed out after 1h")

    _AGNES_FRAME_RATE = 24

    def _num_frames_for_duration(self, duration: int) -> int:
        target = duration * self._AGNES_FRAME_RATE
        frames = target + (1 - target % 8) % 8
        return max(9, min(441, frames))

    def _generate_agnes_apihub(
        self, prompt: str, width: int, height: int, duration: int,
        seed: int | None, output_dir, out_path: Path,
        ref_image: str | None = None,
        ref_images: list[str] | None = None,
    ) -> str:
        apihub_url = self.config.get("apihub_url", "https://apihub.agnes-ai.com").rstrip("/")
        api_key = self.config.get("api_key", "") or os.environ.get("AGNES_API_KEY", "")
        model = self.config.get("video_model", "agnes-video-v2.0")
        if not api_key:
            raise RuntimeError("AGNES_API_KEY manquant pour la vidéo directe")

        num_frames = self._num_frames_for_duration(duration)
        keyframes = ref_images or ([ref_image] if ref_image else None)

        if self.config.get("dev"):
            width = self.config.get("dev_width", width)
            height = self.config.get("dev_height", height)
            logger.info(f"Dev mode: vidéo en {width}x{height} (basse résolution)")

        payload: dict = {
            "model": model,
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "frame_rate": self._AGNES_FRAME_RATE,
        }
        if seed is not None:
            payload["seed"] = seed

        mode = "t2v"
        if keyframes and len(keyframes) > 1:
            mode = "keyframes"
            extra: dict = {"image": [self._to_public_url(k) for k in keyframes], "mode": "keyframes"}
            payload["extra_body"] = extra
        elif keyframes:
            mode = "i2v"
            payload["image"] = self._to_public_url(keyframes[0])

        logger.info(f"Generating video via Agnes apihub: {model} mode={mode} {num_frames}f {width}x{height}")
        max_attempts = self.config.get("apihub_retries", 3)
        base_wait = self.config.get("apihub_retry_base_wait", 10)
        max_wait = self.config.get("apihub_retry_max_wait", 60)
        try:
            resp = None
            for attempt in range(max_attempts):
                resp = requests.post(
                    f"{apihub_url}/v1/videos",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=120,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = min(base_wait * (2 ** attempt), max_wait)
                    logger.warning(f"Agnes apihub {resp.status_code}, retry dans {wait}s...")
                    time.sleep(wait)
                    continue
                break
            if resp is None:
                raise RuntimeError("Agnes apihub: no response after retries")
            resp.raise_for_status()
            data = resp.json()
            video_id = data.get("video_id") or data.get("id") or data.get("task_id")
            if not video_id:
                raise RuntimeError(f"Agnes apihub returned no video_id: {resp.text[:300]}")

            video_url = self._poll_agnes_apihub(apihub_url, api_key, video_id, model)
            video_resp = requests.get(video_url, timeout=300)
            video_resp.raise_for_status()
            if output_dir:
                out_path.write_bytes(video_resp.content)
                return str(out_path)
            return video_url
        except requests.RequestException as e:
            raise RuntimeError(f"Agnes apihub video gen failed: {e}")

    @staticmethod
    def _to_public_url(image_ref: str) -> str:
        if image_ref.startswith(("http://", "https://")):
            return image_ref
        return upload_to_tmpfiles(image_ref)

    def _poll_agnes_apihub(self, apihub_url: str, api_key: str, video_id: str, model: str) -> str:
        import time as _time
        deadline = _time.time() + 3600
        last_status = ""
        while _time.time() < deadline:
            try:
                resp = requests.get(
                    f"{apihub_url}/agnesapi",
                    params={"video_id": video_id, "model_name": model},
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=60,
                )
                resp.raise_for_status()
                task = resp.json()
            except requests.RequestException:
                _time.sleep(15)
                continue
            status = task.get("status", "")
            if status != last_status:
                logger.info(f"Agnes apihub task {video_id}: {status} ({task.get('progress', 0)}%)")
                last_status = status
            if status == "completed":
                url = task.get("url") or (task.get("metadata") or {}).get("url")
                if not url:
                    raise RuntimeError(f"Agnes apihub task {video_id} completed without URL")
                return url
            if status == "failed":
                raise RuntimeError(f"Agnes apihub task {video_id} failed: {task.get('error') or ''}")
            _time.sleep(10)
        raise RuntimeError(f"Agnes apihub task {video_id} timed out after 1h")
