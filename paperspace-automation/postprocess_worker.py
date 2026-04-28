"""
後処理パイプライン Consumer — Limb Check → Upscale → Mosaic → Drive Upload。

Producer (auto_gen_worker.py の生成ループ) から base64 画像を受け取り、
別スレッドで非同期に後処理を実行する。生成を止めない。

処理フロー:
  1. Limb Check (Colab vLLM — GPU 無関係) → 001_Bodycheck/ にアップロード
  2. Upscale (WebUI extra-single-image API — GPU) → 001 Upscaled/ にアップロード
  3. Mosaic (Custom ONNX — CPU only) → 002 mosaic/ にアップロード
"""

import base64
import json
import queue
import threading
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass

import requests


@dataclass
class PostProcessItem:
    images_b64: list[str]
    queue_index: int
    outpath_samples: str


class PostProcessConsumer:

    def __init__(self, config_path: Path, sd_url: str = "http://127.0.0.1:7860"):
        config = json.loads(config_path.read_text())
        self._queue: queue.Queue = queue.Queue(maxsize=16)
        self._thread = None
        self.stats = {"total": 0, "limb_ng": 0, "upscaled": 0, "mosaicked": 0}
        self.lock = threading.Lock()

        self._sd_url = sd_url
        self._session = requests.Session()

        limb_cfg = config.get("limb_check", {})
        upscale_cfg = config.get("upscale", {})
        mosaic_cfg = config.get("mosaic", {})

        self._limb_enabled = limb_cfg.get("enabled", False)
        self._upscale_enabled = upscale_cfg.get("enabled", False)
        self._mosaic_enabled = mosaic_cfg.get("enabled", False)

        self._limb_checker = None
        if self._limb_enabled:
            self._fetch_vision_url()
            try:
                from vision_limb_checker import VisionLimbChecker
                self._limb_checker = VisionLimbChecker(
                    interval=limb_cfg.get("interval", 1.0),
                )
                print("  [PP] Limb checker 初期化完了")
            except Exception as e:
                print(f"  [PP-WARN] Limb checker 初期化失敗（スキップ）: {e}")
                self._limb_enabled = False

        self._upscale_config = upscale_cfg
        self._upscale_timeout = upscale_cfg.get("timeout", 120)

        self._mosaic = None
        if self._mosaic_enabled:
            try:
                from auto_mosaic import AutoMosaicPy
                self._mosaic = AutoMosaicPy(mosaic_cfg)
                print("  [PP] Mosaic 初期化完了")
            except Exception as e:
                print(f"  [PP-WARN] Mosaic 初期化失敗（スキップ）: {e}")
                self._mosaic_enabled = False

        self._gdrive_uploader = None
        try:
            import gdrive_uploader
            self._gdrive_uploader = gdrive_uploader
        except ImportError:
            print("  [PP-WARN] gdrive_uploader 未発見 — Drive アップロードスキップ")

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="PostProcess")
        self._thread.start()

    def enqueue(self, item: PostProcessItem):
        self._queue.put(item)

    def finish(self):
        self._queue.put(None)

    def wait(self):
        if self._thread:
            self._thread.join()

    def _run(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                self._process(item)
            except Exception as e:
                print(f"  [PP-WARN] queue={item.queue_index}: {e}")

    def _process(self, item: PostProcessItem):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bodycheck_b64 = []
            upscaled_b64 = []
            mosaic_b64 = []

            for idx, img_b64 in enumerate(item.images_b64):
                raw_path = tmp / f"{item.queue_index:05d}_{idx:04d}.png"
                raw_path.write_bytes(base64.b64decode(img_b64))

                # 1. Limb Check (Colab vLLM)
                if self._limb_enabled and self._limb_checker:
                    try:
                        result = self._limb_checker.check(raw_path)
                        if not result.ok:
                            with self.lock:
                                self.stats["limb_ng"] += 1
                            print(f"  [{item.queue_index}:{idx}] Limb NG: {result.issues}")
                            continue
                    except Exception as e:
                        print(f"  [{item.queue_index}:{idx}] Limb check error (proceeding): {e}")

                bodycheck_b64.append(img_b64)

                # 2. Upscale (WebUI API)
                source = raw_path
                if self._upscale_enabled:
                    upscaled_path = tmp / f"up_{raw_path.name}"
                    ok = self._upscale_single(raw_path, upscaled_path)
                    if ok:
                        with self.lock:
                            self.stats["upscaled"] += 1
                        source = upscaled_path

                upscaled_b64.append(
                    base64.b64encode(source.read_bytes()).decode()
                )

                # 3. Mosaic (Custom ONNX — CPU)
                mosaic_source = source
                if self._mosaic_enabled and self._mosaic:
                    final_path = tmp / f"final_{raw_path.name}"
                    try:
                        self._mosaic.process_single(source, final_path)
                        with self.lock:
                            self.stats["mosaicked"] += 1
                        mosaic_source = final_path
                    except Exception as e:
                        print(f"  [{item.queue_index}:{idx}] Mosaic error (using pre-mosaic): {e}")

                mosaic_b64.append(
                    base64.b64encode(mosaic_source.read_bytes()).decode()
                )

            # Drive Upload — フェーズごとに別フォルダ
            prefix = f"{item.queue_index:05d}"
            if self._gdrive_uploader:
                self._upload_phase(bodycheck_b64, item.outpath_samples, "bodycheck", prefix)
                self._upload_phase(upscaled_b64, item.outpath_samples, "upscaled", prefix)
                self._upload_phase(mosaic_b64, item.outpath_samples, "mosaic", prefix)

            with self.lock:
                self.stats["total"] += len(mosaic_b64)

    def _upload_phase(self, images_b64: list, outpath: str, phase: str, prefix: str):
        if not images_b64:
            return
        phase_path = _to_phase_path(outpath, phase)
        try:
            self._gdrive_uploader.upload_images_to_drive(
                images_b64, phase_path, filename_prefix=prefix)
        except Exception as e:
            print(f"  [PP-WARN] {phase} Drive UP 失敗: {e}")

    def _upscale_single(self, input_path: Path, output_path: Path) -> bool:
        endpoint = f"{self._sd_url}/sdapi/v1/extra-single-image"
        try:
            b64_img = base64.b64encode(input_path.read_bytes()).decode()
            payload = {
                "upscaling_resize": self._upscale_config.get("upscaling_resize", 2),
                "upscaler_1": self._upscale_config.get("upscaler_1", "R-ESRGAN 4x+ Anime6B"),
                "image": b64_img,
                "show_extras_results": False,
            }
            resp = self._session.post(endpoint, json=payload, timeout=self._upscale_timeout)
            if resp.status_code == 200:
                img_data = base64.b64decode(resp.json()["image"])
                output_path.write_bytes(img_data)
                return True
            print(f"  [PP-WARN] Upscale API {resp.status_code}: {input_path.name}")
            return False
        except Exception as e:
            print(f"  [PP-WARN] Upscale error: {e}")
            return False


    def _fetch_vision_url(self):
        """Drive から最新の vision_url.json を取得して CWD に配置する。"""
        dest = Path.cwd() / "vision_url.json"
        if dest.exists():
            try:
                age_h = (time.time() - dest.stat().st_mtime) / 3600
                if age_h < 1:
                    print(f"  [PP] vision_url.json は {age_h:.1f}h 前に取得済み — スキップ")
                    return
            except Exception:
                pass
        try:
            import gdrive_uploader
            ok = gdrive_uploader.download_config_from_drive("vision_url.json", str(dest))
            if ok:
                data = json.loads(dest.read_text())
                print(f"  [PP] vision_url.json を Drive から取得: {data.get('url', '?')}")
            else:
                print("  [PP-WARN] vision_url.json が Drive に見つからない")
        except Exception as e:
            print(f"  [PP-WARN] vision_url.json 取得失敗: {e}")


_ORIGINAL_FOLDER = "000_Original_プロンプト保管用"

_PHASE_FOLDERS = {
    "bodycheck": "001_Bodycheck",
    "upscaled": "001 Upscaled",
    "mosaic": "002 mosaic",
}


def _to_phase_path(outpath: str, phase: str) -> str:
    folder = _PHASE_FOLDERS[phase]
    if _ORIGINAL_FOLDER in outpath:
        return outpath.replace(_ORIGINAL_FOLDER, folder, 1)
    parts = outpath.replace("\\", "/").split("/")
    try:
        idx = parts.index("outputs")
        parts[idx + 1] = folder
        return "/".join(parts)
    except (ValueError, IndexError):
        return outpath
