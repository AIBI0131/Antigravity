"""
GUI Pipeline Worker Module (PyQt6)
===================================
QObject + moveToThread() パターンを使用した並行処理ワーカー。

GUI版のProducer-Consumerパターンで画像生成中に後処理を並行実行します。
"""

import queue
import re as _re_module
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

# Upscaler, AutoMosaicPy は重いため __init__() 内で遅延インポート


class GUIPostProcessWorker(QObject):
    """
    GUI用の後処理ワーカー（QObject）。

    QThread に moveToThread() して使用します。
    queue.Queue から画像パスを取得し、Upscale → Mosaic を実行します。

    シグナル:
        log_signal(str): ログメッセージ
        progress_signal(str, int, int): (stage, current, total)
        item_processed_signal(str): 処理完了した画像パス
        error_signal(str, str): (error_type, error_message)
        finished_signal(int, int, int): (generated, upscaled, mosaicked)
    """

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str, int, int)  # stage, current, total
    item_processed_signal = pyqtSignal(str)  # processed image path
    error_signal = pyqtSignal(str, str)  # error_type, error_message
    finished_signal = pyqtSignal(int, int, int)  # generated, upscaled, mosaicked
    thread_started_signal = pyqtSignal(int)  # thread_id（stdout 登録用）

    def __init__(self, config: dict, dirs: dict[str, Path], parent=None):
        """
        Parameters
        ----------
        config : dict
            設定辞書（upscale, mosaic セクションを含む）
        dirs : dict[str, Path]
            出力ディレクトリ辞書
        """
        super().__init__(parent)

        self.config = config
        self.dirs = dirs

        # スレッドセーフなキュー
        self.image_queue: queue.Queue = queue.Queue()

        # 統計
        self.total_generated = 0
        self.total_upscaled = 0
        self.total_mosaicked = 0
        self.lock = threading.Lock()

        # EOF マーカー受信フラグ
        self.finished_flag = False

        # 設定に基づいてモジュールを初期化
        self.upscale_enabled = config.get("upscale", {}).get("enabled", False)
        self.mosaic_enabled = config.get("mosaic", {}).get("enabled", False)

        self.upscaler = None
        self.mosaic_processor = None

        if self.upscale_enabled:
            from modules.upscaler import Upscaler
            sd_url = config.get("generation", {}).get("sd_url")
            self.upscaler = Upscaler(config["upscale"], sd_url=sd_url)

        if self.mosaic_enabled:
            from modules.auto_mosaic import AutoMosaicPy
            self.mosaic_processor = AutoMosaicPy(config["mosaic"])

        # Limb Check
        self.limb_check_enabled = config.get("limb_check", {}).get("enabled", False)
        self.limb_checker = None
        self.total_limb_ng = 0

        if self.limb_check_enabled:
            from modules.vision_limb_checker import VisionLimbChecker
            self.limb_checker = VisionLimbChecker()

        # SFX Overlay
        self.sfx_enabled = config.get("sfx", {}).get("enabled", False)
        self.sfx_catalog = None
        self.total_sfx = 0

        if self.sfx_enabled:
            from modules.sfx.sfx_catalog import SfxCatalog
            from modules.sfx.sfx_overlay import resolve_scene_auto, process_image as sfx_process_image
            self.sfx_catalog = SfxCatalog()
            self._sfx_process_image = sfx_process_image
            self._sfx_resolve_scene = resolve_scene_auto
            sfx_cfg = config.get("sfx", {})
            self.sfx_scene = sfx_cfg.get("scene", "auto")
            count_str = sfx_cfg.get("count", "6-10")
            scale_str = sfx_cfg.get("scale", "0.15-0.30")
            self.sfx_count_range = tuple(int(x) for x in count_str.split("-"))
            self.sfx_scale_range = tuple(float(x) for x in scale_str.split("-"))
            self.sfx_raw_root = Path(__file__).resolve().parent.parent / "output" / "raw"

        # タイマーは start_processing() でワーカースレッド内に作成（アフィニティ問題回避）
        self.timer = None

    def start_processing(self):
        """処理開始: タイマーを起動してキューのポーリングを開始する。
        このメソッドはワーカー QThread 内で呼ばれる。
        """
        # このスレッドの print() を Post-Process Log へルーティング
        import sys
        if hasattr(sys.stdout, 'set_thread_signal'):
            sys.stdout.set_thread_signal(self.log_signal)

        # QTimer をワーカースレッド内で作成（スレッドアフィニティを正しく設定）
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._process_queue)

        self.thread_started_signal.emit(threading.current_thread().ident)
        self.log_signal.emit("🔧 後処理ワーカー起動")
        self.timer.start(100)  # 100ms ごとにキューをチェック

    def enqueue(self, image_path: Path):
        """
        画像を処理キューに追加する。

        Parameters
        ----------
        image_path : Path
            生成された画像のパス
        """
        with self.lock:
            self.total_generated += 1

        self.image_queue.put(image_path)

    def finish(self):
        """
        画像生成完了を通知する（EOF マーカー）。
        None をキューに送信してワーカーに終了を知らせる。
        """
        self.image_queue.put(None)

    def _process_queue(self):
        """
        キューから画像を取り出して処理する（タイマーコールバック）。
        非ブロッキングで1つのアイテムを処理します。
        """
        try:
            # 非ブロッキングでキューから取得
            image_path = self.image_queue.get_nowait()

            if image_path is None:
                # EOF マーカーを受信
                self.finished_flag = True
                self.timer.stop()
                self.log_signal.emit(f"✅ 後処理完了: Upscaled={self.total_upscaled}, Mosaicked={self.total_mosaicked}, SFX={self.total_sfx}, LimbNG={self.total_limb_ng}")
                self.finished_signal.emit(self.total_generated, self.total_upscaled, self.total_mosaicked)
                return

            # 画像を処理
            self._process_image(image_path)

        except queue.Empty:
            # キューが空の場合は何もしない
            pass

    def _process_image(self, image_path: Path):
        """
        単一画像の後処理を実行する（Upscale → Mosaic のパイプライン）。

        Parameters
        ----------
        image_path : Path
            処理対象の画像パス
        """
        try:
            current_source = image_path

            # Step 0: Limb Check (before upscale/mosaic — reject NG images early)
            if self.limb_check_enabled and self.limb_checker:
                import shutil
                self.log_signal.emit(f"   🔍 Limb Check: {image_path.name}")
                result = self.limb_checker.check(image_path)
                if not result.ok:
                    ng_dir = self.dirs["raw"].parent / "limb_ng"
                    ng_dir.mkdir(parents=True, exist_ok=True)
                    ng_path = ng_dir / image_path.name
                    shutil.move(str(image_path), str(ng_path))
                    issues_str = " / ".join(result.issues) if result.issues else "unknown"
                    self.log_signal.emit(f"   🚫 Limb NG → limb_ng/: {image_path.name} [{issues_str}]")
                    with self.lock:
                        self.total_limb_ng += 1
                    self.item_processed_signal.emit(str(image_path))
                    return

            # Step 1: Upscale
            if self.upscale_enabled and self.upscaler:
                upscaled_path = self._get_upscaled_path(image_path)

                self.progress_signal.emit("Upscaling", self.total_upscaled + 1, self.total_generated)
                self.log_signal.emit(f"   🔎 Upscaling: {image_path.name}")

                success = self.upscaler.upscale_single(current_source, upscaled_path)

                if success:
                    with self.lock:
                        self.total_upscaled += 1
                    current_source = upscaled_path
                else:
                    self.log_signal.emit(f"   ⚠️ Upscale失敗: {image_path.name}")

            # Step 2: Mosaic
            if self.mosaic_enabled and self.mosaic_processor:
                final_path = self._get_mosaic_path(image_path)
                fill_path = self._get_fill_path(image_path) if self.config["mosaic"].get("save_fill_image", True) else None
                mask_path = self._get_mask_path(image_path) if self.config.get("output", {}).get("save_mask", False) else None
                # 検出なし画像の振り分け先（NoMosaic サブフォルダ）
                nomosaic_path = None
                if self.config["mosaic"].get("is_save_no_mosaic_folder", True):
                    nomosaic_path = final_path.parent / "NoMosaic" / final_path.name

                self.progress_signal.emit("Mosaic", self.total_mosaicked + 1, self.total_generated)
                self.log_signal.emit(f"   🔲 Mosaic: {image_path.name}")

                had_detections = self.mosaic_processor.process_single(
                    current_source, final_path, fill_path, mask_path, nomosaic_path
                )

                if had_detections:
                    with self.lock:
                        self.total_mosaicked += 1
                    current_source = final_path

            # Step 3: SFX Overlay
            if self.sfx_enabled and self.sfx_catalog:
                sfx_path = self._get_sfx_path(image_path)

                scene = self.sfx_scene
                if scene == "auto":
                    scene = self._sfx_resolve_scene(image_path, self.sfx_raw_root)
                    from modules.sfx.sfx_catalog import SCENE_SFX_MAP
                    if scene not in SCENE_SFX_MAP:
                        scene = "any"

                self.progress_signal.emit("SFX", self.total_sfx + 1, self.total_generated)
                self.log_signal.emit(f"   🔤 SFX: {image_path.name} [{scene}]")

                sfx_count = self._sfx_process_image(
                    current_source, sfx_path,
                    self.sfx_catalog, scene,
                    self.sfx_count_range, self.sfx_scale_range,
                )
                if sfx_count > 0:
                    with self.lock:
                        self.total_sfx += 1

            # 処理完了を通知
            self.item_processed_signal.emit(str(image_path))

        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            self.error_signal.emit(type(e).__name__, error_trace)
            self.log_signal.emit(f"   ❌ 後処理エラー ({image_path.name}): {e}")

    def _get_upscaled_path(self, raw_path: Path) -> Path:
        """生成画像パスから対応するアップスケール済みパスを計算する。"""
        try:
            raw_base = self.dirs["raw"]
            try:
                rel_path = raw_path.relative_to(raw_base)
            except ValueError:
                import os
                rel_str = os.path.relpath(str(raw_path), str(raw_base))
                if rel_str.startswith(".."):
                    raise ValueError("Not a subdirectory")
                rel_path = Path(rel_str)

            # サブフォルダ構造を _Upscaled サフィックス付きで再現
            parts = list(rel_path.parent.parts)
            if len(parts) >= 2:
                parts = parts[-2:]

            upscaled_parts = []
            for part in parts:
                base_name = _re_module.sub(r'_(Original|Raw|Upscaled)$', '', part, flags=_re_module.IGNORECASE)
                if not base_name.endswith("_Upscaled"):
                    base_name = f"{base_name}_Upscaled"
                upscaled_parts.append(base_name)

            sub_dir = Path(*upscaled_parts) if upscaled_parts else Path(".")
            ext = self.upscaler.format if hasattr(self.upscaler, 'format') else "png"
            return self.dirs["upscaled"] / sub_dir / f"{raw_path.stem}.{ext}"

        except ValueError:
            return self.dirs["upscaled"] / raw_path.name

    def _get_mosaic_path(self, raw_path: Path) -> Path:
        """生成元画像(raw)のパスから最終出力パスを計算する。"""
        try:
            # サブフォルダ構造の計算基準は常に raw ディレクトリとする
            base = self.dirs["raw"]
            try:
                rel_path = raw_path.relative_to(base)
            except ValueError:
                import os
                rel_str = os.path.relpath(str(raw_path), str(base))
                if rel_str.startswith(".."):
                    raise ValueError("Not a subdirectory")
                rel_path = Path(rel_str)

            parts = list(rel_path.parent.parts)
            if len(parts) >= 2:
                parts = parts[-2:]

            mosaic_parts = []
            for part in parts:
                base_name = _re_module.sub(r'_(Original|Raw|Upscaled|Mosaic)$', '', part, flags=_re_module.IGNORECASE)
                if not base_name.endswith("_Mosaic"):
                    base_name = f"{base_name}_Mosaic"
                mosaic_parts.append(base_name)

            sub_dir = Path(*mosaic_parts) if mosaic_parts else Path(".")
            return self.dirs["final"] / sub_dir / raw_path.name

        except ValueError:
            return self.dirs["final"] / raw_path.name

    def _get_fill_path(self, source_path: Path) -> Path:
        """塗りつぶし画像パスを計算する。"""
        try:
            mosaic_path = self._get_mosaic_path(source_path)
            rel_path = mosaic_path.relative_to(self.dirs["final"])
            return self.dirs["fill"] / rel_path
        except ValueError:
            return self.dirs["fill"] / source_path.name

    def _get_mask_path(self, source_path: Path) -> Path:
        """マスク画像パスを計算する。"""
        try:
            mosaic_path = self._get_mosaic_path(source_path)
            rel_path = mosaic_path.relative_to(self.dirs["final"])
            return self.dirs["mask"] / rel_path
        except ValueError:
            return self.dirs["mask"] / source_path.name

    def _get_sfx_path(self, raw_path: Path) -> Path:
        """SFXオーバーレイ出力パスを計算する。"""
        try:
            base = self.dirs["raw"]
            try:
                rel_path = raw_path.relative_to(base)
            except ValueError:
                import os
                rel_str = os.path.relpath(str(raw_path), str(base))
                if rel_str.startswith(".."):
                    raise ValueError("Not a subdirectory")
                rel_path = Path(rel_str)

            parts = list(rel_path.parent.parts)
            if len(parts) >= 2:
                parts = parts[-2:]

            sfx_parts = []
            for part in parts:
                base_name = _re_module.sub(r'_(Original|Raw|Upscaled|Mosaic|SFX)$', '', part, flags=_re_module.IGNORECASE)
                if not base_name.endswith("_SFX"):
                    base_name = f"{base_name}_SFX"
                sfx_parts.append(base_name)

            sub_dir = Path(*sfx_parts) if sfx_parts else Path(".")
            return self.dirs["final"] / sub_dir / raw_path.name

        except ValueError:
            return self.dirs["final"] / raw_path.name

    def get_stats(self) -> dict:
        """処理統計を取得する。"""
        with self.lock:
            return {
                "generated": self.total_generated,
                "upscaled": self.total_upscaled,
                "mosaicked": self.total_mosaicked,
                "sfx": self.total_sfx,
            }
