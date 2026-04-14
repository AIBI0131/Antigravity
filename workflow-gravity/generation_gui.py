"""
Generation GUI — Workflow-Gravity
=================================
A modern PyQt6 + qt-material interface for Stable Diffusion generation.
Features:
- Auto-detect Gradio URL from clipboard
- Configure SD parameters (Model, VAE, Sampler, Steps, etc.)
- Execute generation seamlessly
"""

import re
import sys
import os
import subprocess
import signal

# --------------------------------------------------------------------------------
# CRITICAL: Import ONNX Runtime BEFORE PyQt6 to prevent DLL load failed errors.
# This fixes the "ImportError: DLL load failed" when both libraries use conflicting
# VC++ Redistributable DLLs or similar system resources.
try:
    import onnxruntime
except ImportError:
    pass # It's optional until Mosaic is used
# --------------------------------------------------------------------------------

import yaml
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import datetime
from pathlib import Path
from typing import Optional
from io import StringIO
import random
import threading
import copy
import queue as stdlib_queue
import re as _re_module  # PostProcessWorker のホットループ内でからの import を回避


from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QFormLayout, QLineEdit, QPushButton, QTextEdit,
    QComboBox, QSlider, QSpinBox, QDoubleSpinBox, QLabel, QSplitter,
    QCheckBox, QMessageBox, QProgressBar, QDialog, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

from qt_material import apply_stylesheet
from modules.sd_client import SDClient, DEFAULT_HEADERS
# Upscaler, AutoMosaicPy, GUIPostProcessWorker は重いため使用箇所で遅延インポート

# ─── Configuration ──────────────────────────────
APP_BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_CONFIG_PATH = APP_BASE_DIR / "config.yaml"

def ensure_dirs(base_dir: Path) -> dict[str, Path]:
    """Create output directory structure."""
    dirs = {
        "raw": base_dir / "raw",
        "upscaled": base_dir / "upscaled",
        "final": base_dir / "final",
        "fill": base_dir / "fill",
        "mask": base_dir / "mask",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs

class ThreadLocalRedirector:
    """threading.get_ident() ベースの stdout リダイレクタ。
    各スレッドが thread_signal を設定することで、print() 出力を
    自動的に適切なログタブへルーティングする。
    QThread の _DummyThread GC による消失対策として id辞書 を使用。
    """
    def __init__(self, default_signal=None):
        self.default_signal = default_signal
        import threading
        self._thread_signals = {}
        self._lock = threading.Lock()

    def set_default_signal(self, signal):
        self.default_signal = signal

    def set_thread_signal(self, signal):
        import threading
        ident = threading.get_ident()
        with self._lock:
            self._thread_signals[ident] = signal

    def write(self, text):
        if text:
            import threading
            ident = threading.get_ident()
            with self._lock:
                signal = self._thread_signals.get(ident, self.default_signal)
            
            if signal:
                signal.emit(str(text))
            else:
                import sys
                sys.__stdout__.write(str(text))

    def flush(self):
        pass

# 一度だけ生成し、アプリ全体で使い回す
global_stdout_redirector = ThreadLocalRedirector()
import sys
sys.stdout = global_stdout_redirector
sys.stderr = global_stdout_redirector


from dataclasses import dataclass, field

@dataclass
class QueueItem:
    """キューに積まれる1ジョブ分のスナップショット。"""
    positive_prompt: str
    negative_prompt: str
    config: dict
    skip_generation: bool = False

class GenerationWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_update_signal = pyqtSignal(str, int, int) # stage, current, total
    # finished(success, message, saved_dirs)
    finished = pyqtSignal(bool, str, object)

    def __init__(self, config: dict, positive_prompt, negative_prompt, skip_generation=False, stop_event=None, on_image_saved=None, redirector=None):
        super().__init__()
        self.config = config
        self.positive_prompt = positive_prompt
        self.negative_prompt = negative_prompt
        self.skip_generation = skip_generation
        self._stop_event = stop_event or threading.Event()
        self.on_image_saved = on_image_saved
        self.redirector = redirector

    def run(self):
        # Setup thread-local redirection
        import sys
        if hasattr(sys.stdout, 'set_thread_signal'):
            sys.stdout.set_thread_signal(self.log_signal)
        # このスレッドの print() を Generation Log へルーティング

        saved_dirs = set()
        try:
            # 1. Setup Directories
            base_dir = Path(self.config["output"].get("base_dir", "output"))
            dirs = ensure_dirs(base_dir)

            # Initialize Client
            client = SDClient(self.config["generation"])

            # Construct Prompts
            pos_lines = [l.strip() for l in self.positive_prompt.split('\n') if l.strip()]
            if not pos_lines:
                pos_lines = [""]

            neg = self.negative_prompt.replace("\n", " ").strip()

            # Determine Start Seed
            gui_seed = int(self.config["generation"]["payload"].get("seed", -1))
            batch_count = self.config.get("_gui_batch_count", 1)

            import random
            current_seed = gui_seed
            if current_seed == -1:
                current_seed = random.randint(0, 2**32 - 1)

            final_prompts = []
            for _ in range(batch_count):
                for pos in pos_lines:
                    p_line = pos
                    if neg:
                        p_line += f" --negative_prompt \"{neg}\""
                    p_line += f" --seed {current_seed}"
                    final_prompts.append(p_line)
                    current_seed += 1

            if not self.skip_generation:
                self.log_signal.emit(f"🚀 Starting Generation: {len(final_prompts)} tasks (Prompts: {len(pos_lines)}, Batch Count: {batch_count})")
                count, saved_dirs = client.generate_batch(final_prompts, dirs["raw"], stop_event=self._stop_event, on_image_saved=self.on_image_saved)
                if count == 0:
                    self.finished.emit(False, "No images generated.", set())
                    return
            else:
                self.log_signal.emit("⏩ Generation skipped. Processing existing images in 'raw'.")
                count = len(list(dirs["raw"].glob("*.png")))
                saved_dirs = {dirs["raw"]}
                if count == 0:
                    self.finished.emit(False, "No existing images found in 'raw' to process.", set())
                    return

            self.finished.emit(True, f"Generation complete. {count} images saved.", saved_dirs)

        except Exception as e:
            import traceback
            self.log_signal.emit(traceback.format_exc())
            self.finished.emit(False, str(e), saved_dirs)
        finally:
            pass  # sys.stdout はリダイレクタのまま維持（後処理が継続中の可能性があるため）


class PostProcessWorker(QThread):
    """アップスケール + モザイクを担当するワーカー（GenerationWorkerと並行実行可能）。"""
    log_signal = pyqtSignal(str)
    progress_update_signal = pyqtSignal(str, int, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, config: dict, saved_dirs: set, stop_event=None):
        super().__init__()
        self.config = config
        self.saved_dirs = saved_dirs
        self._stop_event = stop_event or threading.Event()

    def run(self):
        # PostProcessWorker は GenerationWorker と並行実行されるため、
        # sys.stdout/stderr のグローバル山書きは竞合を引き起こす。
        # ログは log_signal.emit() のみで行い、print()は使わない。
        try:
            base_dir = Path(self.config["output"].get("base_dir", "output"))
            dirs = ensure_dirs(base_dir)

            def _upscale_progress(msg, *args):
                self.progress_update_signal.emit("Upscaling", 0, 0)
                self.log_signal.emit(str(msg))

            def _mosaic_progress(current, total, filename):
                self.progress_update_signal.emit("Mosaic", current, total)

            if self._stop_event.is_set():
                self.finished.emit(False, "Stopped before post-processing.")
                return

            # --- UPSCALE ---
            if self.config.get("upscale", {}).get("enabled", False):
                self.log_signal.emit("\n🔎 Starting Upscale...")
                sd_url = self.config.get("generation", {}).get("sd_url")
                upscale_cfg = self.config["upscale"]
                if upscale_cfg.get("mode") == "api" and not upscale_cfg.get("api", {}).get("upscaler_1"):
                    upscale_cfg.setdefault("api", {})["upscaler_1"] = "R-ESRGAN 4x+ Anime6B"
                from modules.upscaler import Upscaler
                upscaler = Upscaler(upscale_cfg, sd_url=sd_url)
                upscaled_count = 0
                for source_dir in self.saved_dirs:
                    if not source_dir.exists():
                        continue
                    c = upscaler.upscale_batch(
                        source_dir, dirs["upscaled"],
                        scale=upscale_cfg.get("scale", 4),
                        progress_callback=_upscale_progress,
                        stop_event=self._stop_event
                    )
                    upscaled_count += c
                self.log_signal.emit(f"   ✅ Upscaled {upscaled_count} images.")
            else:
                self.log_signal.emit("\n⏭️  Skipping Upscale.")

            if self._stop_event.is_set():
                self.finished.emit(False, "Stopped after upscale.")
                return

            # --- MOSAIC ---
            if self.config.get("mosaic", {}).get("enabled", False):
                self.log_signal.emit("\n🔲 Starting Auto-Mosaic...")
                from modules.auto_mosaic import AutoMosaicPy
                mosaic = AutoMosaicPy(self.config["mosaic"])
                mosaic_cfg = self.config["mosaic"]
                fill_dir = dirs["fill"] if mosaic_cfg.get("save_fill_image", True) else None
                mask_dir = dirs["mask"] if self.config["output"].get("save_mask", True) else None

                processed_count = 0
                for original_dir in self.saved_dirs:
                    mosaic_source = original_dir
                    if self.config.get("upscale", {}).get("enabled", False):
                        try:
                            rel_path = original_dir.relative_to(dirs["raw"])
                            parts = list(rel_path.parts)
                            if len(parts) >= 2:
                                parts = parts[-2:]
                            for i in range(len(parts)):
                                b = _re_module.sub(r'_(Original|Raw|Upscaled)$', '', parts[i], flags=_re_module.IGNORECASE)
                                if not b.endswith("_Upscaled"):
                                    parts[i] = f"{b}_Upscaled"
                            mosaic_source = dirs["upscaled"] / Path(*parts)
                        except ValueError:
                            mosaic_source = dirs["upscaled"]
                    if not mosaic_source.exists():
                        continue
                    c = mosaic.process_batch(
                        mosaic_source, dirs["final"], fill_dir, mask_dir,
                        progress_callback=_mosaic_progress,
                        stop_event=self._stop_event
                    )
                    processed_count += c
                self.log_signal.emit(f"   ✅ Mosaiced {processed_count} images.")
            else:
                self.log_signal.emit("\n⏭️  Skipping Mosaic.")

            self.finished.emit(True, "Post-processing complete.")

        except Exception as e:
            import traceback
            self.log_signal.emit(traceback.format_exc())
            self.finished.emit(False, str(e))

class FetchOptionsWorker(QThread):
    finished = pyqtSignal(dict) # {key: [list]}


    def __init__(self, base_url, auth=None):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self._headers = DEFAULT_HEADERS

    def run(self):
        data = {"models": [], "vaes": [], "samplers": [], "schedulers": [], "error": None}
        try:
            # Models
            target_url = f"{self.base_url}/sdapi/v1/sd-models"
            resp = requests.get(target_url, timeout=10, headers=self._headers, auth=self.auth, verify=False)

            if resp.status_code == 200:
                data["models"] = sorted([m["title"] for m in resp.json()])
            else:
                data["error"] = f"Models Fetch Failed: {resp.status_code} {resp.reason}"
                self.finished.emit(data)
                return
            
            # VAEs
            resp = requests.get(f"{self.base_url}/sdapi/v1/sd-vae", timeout=10, headers=self._headers, auth=self.auth, verify=False)
            if resp.status_code == 200:
                data["vaes"] = sorted([v["model_name"] for v in resp.json()])

            # Samplers
            resp = requests.get(f"{self.base_url}/sdapi/v1/samplers", timeout=10, headers=self._headers, auth=self.auth, verify=False)
            if resp.status_code == 200:
                data["samplers"] = [s["name"] for s in resp.json()]

            # Schedulers
            resp = requests.get(f"{self.base_url}/sdapi/v1/schedulers", timeout=10, headers=self._headers, auth=self.auth, verify=False)
            if resp.status_code == 200:
                data["schedulers"] = [s["name"] for s in resp.json()]
                
        except Exception as e:
            data["error"] = str(e)
            
        self.finished.emit(data)

class ProgressWorker(QThread):
    progress_signal = pyqtSignal(int, int, float, float) # step, total, progress_float, eta
    finished = pyqtSignal()


    def __init__(self, base_url, auth=None):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.is_running = True
        self._headers = DEFAULT_HEADERS

    def run(self):
        import time
        while self.is_running:
            try:
                resp = requests.get(f"{self.base_url}/sdapi/v1/progress", timeout=3, headers=self._headers, auth=self.auth)
                if resp.status_code == 200:
                    data = resp.json()
                    state = data.get("state", {})
                    # ... processing ...
                    step = state.get("sampling_step", 0)
                    total = state.get("sampling_steps", 0)
                    progress = data.get("progress", 0.0)
                    eta = data.get("eta_relative", 0.0)
                    
                    self.progress_signal.emit(step, total, progress, eta)
                elif resp.status_code == 404:
                    # API disabled or endpoint missing -> Silence
                    pass
            except (requests.exceptions.RequestException, ValueError):
                pass
            except Exception:
                pass # Silence all progress errors to avoid log spam
            
            time.sleep(0.5)
        self.finished.emit()

    def stop(self):
        self.is_running = False

class LinkReceiver(QThread):
    """Background HTTP server to receive URLs from Chrome Extension."""
    url_received = pyqtSignal(str)

    def run(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import json

        receiver_signal = self.url_received

        class RequestHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == '/update_url':
                    content_length = int(self.headers['Content-Length'])
                    post_data = self.rfile.read(content_length)
                    try:
                        data = json.loads(post_data)
                        url = data.get('url')
                        if url:
                            receiver_signal.emit(url)
                        
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        # CORS headers for Chrome Extension
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(b'{"status":"ok"}')
                    except Exception as e:
                        print(f"Receiver Error: {e}")
                        self.send_response(400)
                        self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def log_message(self, format, *args):
                pass # Silence console logs

        try:
            server = HTTPServer(('localhost', 18000), RequestHandler)
            server.serve_forever()
        except Exception as e:
            print(f"Failed to start LinkReceiver: {e}")

class UpscaleSettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Upscale Settings")
        self.config = config
        self.resize(400, 300)
        
        layout = QFormLayout(self)
        
        # Mode
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["cli", "api"])
        self.combo_mode.setCurrentText(self.config.get("mode", "cli"))
        layout.addRow("Mode:", self.combo_mode)
        
        # Scale
        self.combo_scale = QComboBox()
        self.combo_scale.addItems(["2x", "3x", "4x"])
        self.combo_scale.setCurrentText(f"{self.config.get('scale', 4)}x")
        layout.addRow("Scale:", self.combo_scale)

        # CLI Settings
        self.group_cli = QGroupBox("CLI Settings")
        layout_cli = QFormLayout(self.group_cli)
        self.txt_model = QLineEdit(self.config.get("cli", {}).get("model", "remacri"))
        layout_cli.addRow("Model Name:", self.txt_model)
        self.txt_exe = QLineEdit(self.config.get("cli", {}).get("executable", ""))
        layout_cli.addRow("Executable Path:", self.txt_exe)
        layout.addRow(self.group_cli)
        
        # API Settings
        self.group_api = QGroupBox("API Settings")
        layout_api = QFormLayout(self.group_api)
        self.txt_api_model = QLineEdit(self.config.get("api", {}).get("upscaler_1", "R-ESRGAN 4x+ Anime6B"))
        layout_api.addRow("API Model:", self.txt_api_model)
        layout.addRow(self.group_api)
        
        # Buttons
        btn_box = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_save)
        btn_box.addWidget(btn_cancel)
        layout.addRow(btn_box)
        
        self.combo_mode.currentTextChanged.connect(self._toggle_groups)
        self._toggle_groups(self.combo_mode.currentText())

    def _toggle_groups(self, text):
        self.group_cli.setVisible(text == "cli")
        self.group_api.setVisible(text == "api")

    def get_settings(self):
        return {
            "mode": self.combo_mode.currentText(),
            "scale": int(self.combo_scale.currentText().replace("x", "")),
            "cli": {
                "model": self.txt_model.text(),
                "executable": self.txt_exe.text()
            },
            "api": {
                "upscaler_1": self.txt_api_model.text()
            }
        }



class AuthDialog(QDialog):
    def __init__(self, current_auth="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Basic Auth")
        self.resize(300, 150)
        layout = QFormLayout(self)
        
        self.txt_user = QLineEdit()
        self.txt_pass = QLineEdit()
        self.txt_pass.setEchoMode(QLineEdit.EchoMode.Password)
        
        if current_auth and ":" in current_auth:
            u, p = current_auth.split(":", 1)
            self.txt_user.setText(u)
            self.txt_pass.setText(p)
            
        layout.addRow("Username:", self.txt_user)
        layout.addRow("Password:", self.txt_pass)
        
        btn_box = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_save)
        btn_box.addWidget(btn_cancel)
        layout.addRow(btn_box)
        
    def get_auth_string(self):
        u = self.txt_user.text().strip()
        p = self.txt_pass.text().strip()
        if u and p:
            return f"{u}:{p}"
        return ""


class GenerationWindow(QMainWindow):
    def __init__(self, config_path: Path = None):
        super().__init__()
        if config_path:
            self._config_path = Path(config_path)
            if not self._config_path.is_absolute():
                self._config_path = APP_BASE_DIR / self._config_path
        else:
            self._config_path = DEFAULT_CONFIG_PATH
            
        config_label = self._config_path.stem  # e.g. "config_colab"
        self.setWindowTitle(f"Workflow-Gravity Generation [{config_label}]")
        self.resize(1200, 900)

        # LoadConfig
        self.config = self._load_config()
        
        # Ensure key config sections exist
        for key in ["generation", "output", "upscale", "mosaic"]:
            if key not in self.config:
                self.config[key] = {}
                
        self.gen_config = self.config["generation"]
        if "payload" not in self.gen_config:
            self.gen_config["payload"] = {}
        self.payload = self.gen_config["payload"]
        
        # UI Setup
        self._build_ui()
        self._load_values()
        
        # Clipboard Setup
        self._setup_clipboard()
        
        # Connect Signals
        self._connect_signals()

        # Queue / concurrency state
        self._job_queue: stdlib_queue.Queue = stdlib_queue.Queue()
        self._post_workers: list = []   # PostProcessWorker リスト（GC防止）
        self._is_running: bool = False  # 現在 GenerationWorker が動いているか

        # 並行処理ワーカー（QObject + moveToThread パターン）
        self._parallel_worker = None  # GUIPostProcessWorker (遅延インポート)
        self._parallel_worker_thread: Optional[QThread] = None
        self._stdout_redirector: Optional[ThreadLocalRedirector] = None

        # Config Save Timer (Debounce)
        self.save_timer = QTimer()
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(100) # 100ms debounce (Instant Save)
        self.save_timer.timeout.connect(self._save_config_to_disk)
        
        # Trigger initial fetch if URL exists and is not a dummy placeholder
        # ウィンドウ表示後に遅延実行（起動高速化）
        startup_url = self.gen_config.get("sd_url", "")
        if startup_url and "xxxxxxxx" not in startup_url:
            QTimer.singleShot(200, self._refresh_options)

        # Start Link Receiver (Colab Extension)
        self.link_receiver = LinkReceiver()
        self.link_receiver.url_received.connect(self._on_external_link_received)
        self.link_receiver.start()

    def closeEvent(self, event):
        """Force save on exit. Also wait for any running post-process workers."""
        self._save_config_to_disk()

        # 並行処理ワーカーを停止
        if self._parallel_worker_thread and self._parallel_worker_thread.isRunning():
            self._parallel_worker_thread.quit()
            self._parallel_worker_thread.wait(3000)  # 最大3秒

        # 終了中の後処理ワーカーを待機
        for w in list(self._post_workers):
            w.wait(3000)  # 最大3秒
        event.accept()

    _last_connected_url: str = ""

    def _save_sd_url_json(self, url: str):
        """sd_url.json にURLを保存し、SDClient との同期を保つ。"""
        import json as _json, time as _time
        sd_url_path = Path(__file__).resolve().parent.parent / "sd_url.json"
        data = {"url": url, "timestamp": int(_time.time())}
        try:
            sd_url_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            self._log(f"⚠️ sd_url.json 保存失敗: {e}")

    def _on_url_editing_finished(self):
        """URL入力欄でEnter押下 or フォーカス移動時に自動接続。"""
        url = self.url_input.text().strip()
        if url and url != self._last_connected_url:
            self._last_connected_url = url
            self.gen_config["sd_url"] = url
            self._save_sd_url_json(url)
            self._log(f"🔗 URL changed, connecting: {url}")
            self._refresh_options()

    def _on_external_link_received(self, url):
        """Handle URL received from Chrome Extension."""
        self._log(f"🔗 Received URL from Colab: {url}")
        if self.url_input.text() != url:
            self.url_input.setText(url)
            self.gen_config["sd_url"] = url
            self._save_sd_url_json(url)
            self._last_connected_url = url
            self._refresh_options()

            # Flash button text
            original = self.btn_paste.text()
            self.btn_paste.setText("⚡ Colab Linked!")
            QTimer.singleShot(3000, lambda: self.btn_paste.setText(original))

    def _connect_signals(self):
        """Connect UI changes to config."""
        # Settings
        self.combo_checkpoint.currentTextChanged.connect(lambda t: self._update_config("checkpoint", t))
        self.combo_vae.currentTextChanged.connect(lambda t: self._update_config("vae", t))
        self.combo_sampler.currentTextChanged.connect(lambda t: self._update_config("sampler", t))
        self.combo_scheduler.currentTextChanged.connect(lambda t: self._update_config("scheduler", t))
        
        self.spin_steps.valueChanged.connect(lambda v: self._update_config("steps", v))
        self.spin_width.valueChanged.connect(lambda v: self._update_config("width", v))
        self.spin_height.valueChanged.connect(lambda v: self._update_config("height", v))
        self.spin_cfg.valueChanged.connect(lambda v: self._update_config("cfg", v))
        self.spin_batch_size.valueChanged.connect(lambda v: self._update_config("batch_size", v))
        self.spin_batch_count.valueChanged.connect(lambda v: self._update_config("batch_count", v))
        self.spin_seed.valueChanged.connect(lambda v: self._update_config("seed", v))
        
        # Post-Processing
        self.chk_upscale.clicked.connect(lambda: self._handle_post_processing_toggle("upscale"))
        self.combo_upscale_scale = QComboBox()
        self.combo_upscale_scale.addItems(["2x", "3x", "4x"])
        self.combo_upscale_scale.setCurrentText(str(self.gen_config.get("upscale", {}).get("scale", "4x")))
        self.combo_upscale_scale.currentTextChanged.connect(lambda t: self._update_config("upscale_scale", t))
        
        self.chk_mosaic.clicked.connect(lambda: self._handle_post_processing_toggle("mosaic"))
        self.chk_sfx.clicked.connect(lambda: self._handle_post_processing_toggle("sfx"))
        self.chk_limb_check.clicked.connect(lambda: self._handle_post_processing_toggle("limb_check"))

        # Text Changes
        self.url_input.textChanged.connect(lambda t: self._update_config("sd_url", t.strip()))
        self.url_input.editingFinished.connect(self._on_url_editing_finished)
        self.txt_prompt.textChanged.connect(lambda: self._update_config("prompt", self.txt_prompt.toPlainText()))
        self.txt_neg_prompt.textChanged.connect(lambda: self._update_config("negative_prompt", self.txt_neg_prompt.toPlainText()))
        
        # Generation
        self.btn_generate.clicked.connect(self._on_generate)
        self.btn_stop.clicked.connect(self._on_stop)

    def _handle_post_processing_toggle(self, mode: str):
        """Handle toggles with dependency checks."""
        boxes = {"upscale": self.chk_upscale, "mosaic": self.chk_mosaic, "sfx": self.chk_sfx, "limb_check": self.chk_limb_check}
        box = boxes[mode]
        is_checked = box.isChecked()
        
        if is_checked:
            # Run check
            if not self._check_dependencies(mode):
                box.setChecked(False) # Revert
                self._update_config(f"{mode}_enabled", False)
                return

        self._update_config(f"{mode}_enabled", is_checked)

    def _check_dependencies(self, mode: str) -> bool:
        """Verify dependencies for Upscale/Mosaic."""
        if mode == "mosaic":
            try:
                import nudenet
                import onnxruntime
            except ImportError as e:
                QMessageBox.warning(self, "Missing Dependency", 
                    f"Auto Mosaic requires extra libraries.\nError: {e}\n\nPlease install: pip install nudenet onnxruntime")
                return False
        elif mode == "upscale":
            # Check if CLI mode and executable exists
            cfg = self.config.get("upscale", {})
            if cfg.get("mode", "cli") == "cli":
                exe = cfg.get("cli", {}).get("executable")
                if not exe or not Path(exe).exists():
                     QMessageBox.warning(self, "Upscayl Not Found",
                        f"Upscayl executable not found at:\n{exe}\n\nPlease configure it via Upscale Settings (⚙) or check {self._config_path.name}.")
                     return False
        elif mode == "sfx":
            try:
                from modules.sfx.sfx_catalog import SfxCatalog
                cat = SfxCatalog()
                if cat.total_count == 0:
                    QMessageBox.warning(self, "SFX Not Found", "SFX素材が見つかりません。")
                    return False
            except Exception as e:
                QMessageBox.warning(self, "SFX Error", f"SFXモジュール初期化エラー:\n{e}")
                return False
        elif mode == "limb_check":
            import json
            url_path = Path(__file__).parent.parent / "vision_url.json"
            try:
                data = json.loads(url_path.read_text(encoding="utf-8"))
                if not data.get("url"):
                    raise ValueError("url が空です")
            except FileNotFoundError:
                QMessageBox.warning(self, "VLM URL 未設定",
                    f"vision_url.json が見つかりません。\n{url_path}\n\n"
                    "VLM サーバーを起動し vision_url.json にURLを記載してください。")
                return False
            except Exception as e:
                QMessageBox.warning(self, "Limb Check 設定エラー", f"vision_url.json 読み込み失敗:\n{e}")
                return False
        return True

    def _update_config(self, key, value):
        # ... (same as before)
        """Update internal config dict and schedule save."""
        if key == "checkpoint":
            if "override_settings" not in self.gen_config:
                self.gen_config["override_settings"] = {}
            self.gen_config["override_settings"]["sd_model_checkpoint"] = value
        elif key == "vae":
            if "override_settings" not in self.gen_config:
                self.gen_config["override_settings"] = {}
            self.gen_config["override_settings"]["sd_vae"] = value
        elif key == "sampler":
            self.payload["sampler_name"] = value
        elif key == "scheduler":
            self.payload["scheduler"] = value
        elif key == "steps":
            self.payload["steps"] = value
        elif key == "width":
            self.payload["width"] = value
        elif key == "height":
            self.payload["height"] = value
        elif key == "cfg":
            self.payload["cfg_scale"] = value
        elif key == "batch_size":
            self.payload["batch_size"] = value
        elif key == "batch_count":
            self.config["_gui_batch_count"] = value
        elif key == "seed":
            self.payload["seed"] = value
        elif key == "prompt":
            self.payload["prompt"] = value
        elif key == "negative_prompt":
            self.payload["negative_prompt"] = value
        elif key == "upscale_enabled":
            self.config["upscale"]["enabled"] = value
        elif key == "upscale_scale":
            # "2x" → 2 に変換して保存
            try:
                self.config["upscale"]["scale"] = int(str(value).replace("x", ""))
            except ValueError:
                pass
        elif key == "mosaic_enabled":
            self.config["mosaic"]["enabled"] = value
        elif key == "sfx_enabled":
            self.config.setdefault("sfx", {})["enabled"] = value
        elif key == "limb_check_enabled":
            self.config.setdefault("limb_check", {})["enabled"] = value
        elif key == "sd_url":
            self.gen_config["sd_url"] = value
            
        # Debounced Save
        self.save_timer.start()

    def _save_config_to_disk(self):
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            print(f"Failed to save config: {e}")

    # ── Queue Management ─────────────────────────────────────

    def _enqueue_job(self):
        """現在の入力内容をキューに追加する。config は deepcopy でスナップショット化。"""
        try:
            fresh_config = self._load_config()
            if "mosaic" in fresh_config:
                self.config["mosaic"] = fresh_config["mosaic"]
        except Exception as e:
            self._log(f"⚠️ Failed to reload config: {e}")

        item = QueueItem(
            positive_prompt=self.txt_prompt.toPlainText(),
            negative_prompt=self.txt_neg_prompt.toPlainText(),
            config=copy.deepcopy(self.config),  # 各ジョブが独自の設定スナップショットを持つ
            skip_generation=self.chk_skip_gen.isChecked()
        )
        self._job_queue.put(item)
        self._update_queue_badge()
        self._log(f"📥 キューに追加: {self._job_queue.qsize()} 件待機中")

    def _start_next_job(self):
        """キューから次のジョブを取り出して GenerationWorker を起動する。"""
        if self._job_queue.empty():
            self._is_running = False
            self.btn_generate.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self._update_queue_badge()
            return

        item: QueueItem = self._job_queue.get_nowait()
        self._is_running = True
        self.btn_stop.setEnabled(True)
        self._update_queue_badge()

        self._stop_event = threading.Event()

        # ── 並行処理ワーカーの初期化 ───────────────────────
        # Upscale または Mosaic が有効な場合、並行処理を使用
        use_parallel = (
            not item.skip_generation and
            (item.config.get("upscale", {}).get("enabled", False) or
             item.config.get("mosaic", {}).get("enabled", False) or
             item.config.get("sfx", {}).get("enabled", False))
        )

        on_image_saved_callback = None
        if use_parallel:
            # 既存の並行ワーカーをクリーンアップ
            if self._parallel_worker_thread and self._parallel_worker_thread.isRunning():
                self._parallel_worker_thread.quit()
                self._parallel_worker_thread.wait()

            # ディレクトリを準備
            base_dir_str = item.config["output"].get("base_dir", "output")
            base_dir = Path(base_dir_str)
            if not base_dir.is_absolute():
                base_dir = APP_BASE_DIR / base_dir
            dirs = ensure_dirs(base_dir)

            # GUIPostProcessWorker を作成（遅延インポート）
            from modules.gui_pipeline_worker import GUIPostProcessWorker
            self._parallel_worker = GUIPostProcessWorker(item.config, dirs)
            self._parallel_worker_thread = QThread()
            self._parallel_worker.moveToThread(self._parallel_worker_thread)

            # シグナル接続（後処理ログは Post-Process タブへ）
            # QueuedConnection を明示: ワーカースレッドからのシグナルをメインスレッドで安全に処理
            Q = Qt.ConnectionType.QueuedConnection
            self._parallel_worker.log_signal.connect(self._log_post, Q)
            self._parallel_worker.progress_signal.connect(self._on_pipeline_progress, Q)
            self._parallel_worker.finished_signal.connect(self._on_parallel_worker_finished, Q)
            self._parallel_worker.error_signal.connect(self._on_parallel_worker_error, Q)
            self._parallel_worker.thread_started_signal.connect(self._register_post_process_thread, Q)

            # スレッド開始時に処理開始
            self._parallel_worker_thread.started.connect(self._parallel_worker.start_processing)

            # スレッド起動
            self._parallel_worker_thread.start()

            # コールバックを設定
            on_image_saved_callback = self._parallel_worker.enqueue

            self._log("🔧 並行処理モード: 生成中に後処理を実行します")

        # ProgressWorker 起動
        if hasattr(self, 'progress_worker') and self.progress_worker.isRunning():
            self.progress_worker.stop()
            self.progress_worker.wait()
        url = self.gen_config.get("sd_url")
        auth = None
        sd_auth = self.gen_config.get("sd_auth", "")
        if sd_auth and ":" in sd_auth:
            auth = tuple(sd_auth.split(":", 1))
        if url:
            self.progress_worker = ProgressWorker(url, auth=auth)
            self.progress_worker.progress_signal.connect(self._update_progress)
            self.progress_worker.start()

        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Initializing...")

        self.worker = GenerationWorker(
            item.config,
            item.positive_prompt,
            item.negative_prompt,
            skip_generation=item.skip_generation,
            stop_event=self._stop_event,
            on_image_saved=on_image_saved_callback
        )
        Q = Qt.ConnectionType.QueuedConnection
        self.worker.log_signal.connect(self._log_gen, Q)

        # ThreadLocalRedirector: 各スレッドが set_thread_signal で自身のルーティング先を設定
        import sys
        if hasattr(sys.stdout, 'set_default_signal'):
            sys.stdout.set_default_signal(self.worker.log_signal)
        if hasattr(sys.stdout, 'set_thread_signal'):
            sys.stdout.set_thread_signal(self.worker.log_signal)
        
        self.worker.progress_update_signal.connect(self._on_pipeline_progress, Q)
        self.worker.finished.connect(self._on_generation_finished, Q)
        self.worker.start()

        remaining = self._job_queue.qsize()
        self._log(f"🚀 生成開始 (残りキュー: {remaining} 件)")

    def _update_queue_badge(self):
        """キュー数をバッジラベルに反映する。"""
        n = self._job_queue.qsize()
        if hasattr(self, 'lbl_queue'):
            self.lbl_queue.setText(f"Queue: {n}" if n > 0 else "")

    # ─────────────────────────────────────────────────────────

    def _on_generate(self):
        """Generate ボタン: キューに追加し、アイドルなら即座に開始。"""
        self._enqueue_job()
        if not self._is_running:
            self.txt_log_gen.clear()
            self.txt_log_post.clear()
            self._start_next_job()

    def _update_progress(self, step, total, progress_float, eta):
        # Only update if we are in "Generation" phase (implied strictly by ProgressWorker running)
        # But ProgressWorker runs only during generation usually. 
        # We can just update unconditionally, as ProgressWorker will stop eventually.
        val = int(progress_float * 100)
        self.progress_bar.setValue(val)
        self.progress_bar.setFormat(f"Progress: {val}% (Step {step}/{total}) - ETA: {int(eta)}s")

    def _on_pipeline_progress(self, stage, current, total):
        """Handle non-SD progress (Upscale/Mosaic)."""
        # Calculate percentage
        if total > 0:
            pct = int((current / total) * 100)
        else:
            pct = 0
        self.progress_bar.setValue(pct)

        # 並行処理モードの場合は統合的な進捗表示
        if self._parallel_worker:
            stats = self._parallel_worker.get_stats()
            gen = stats["generated"]
            up = stats["upscaled"]
            mos = stats["mosaicked"]

            # どちらの処理が有効かに応じて表示を変更
            parts = [f"Gen: {gen}"]
            if self.config.get("upscale", {}).get("enabled", False):
                parts.append(f"Upscale: {up}/{gen}")
            if self.config.get("mosaic", {}).get("enabled", False):
                parts.append(f"Mosaic: {mos}/{gen}")

            self.progress_bar.setFormat(f"Pipeline ({', '.join(parts)})")
        else:
            # 従来の表示
            self.progress_bar.setFormat(f"{stage}: {pct}% ({current}/{total})")

    def _on_stop(self):
        """ユーザーによる停止リクエスト。現在実行中のジョブを中断し、キューもクリアする。"""
        self._stop_event.set()
        # キューを全クリア
        cleared = 0
        while not self._job_queue.empty():
            try:
                self._job_queue.get_nowait()
                cleared += 1
            except stdlib_queue.Empty:
                break
        self.btn_stop.setEnabled(False)
        self._update_queue_badge()
        msg = "⚠️ 停止リクエストを送信しました"
        if cleared:
            msg += f"（キュー {cleared} 件もキャンセル）"
        self._log(msg)

        # サーバー側の生成も中断(SD WebUI API)
        url = self.url_input.text().strip()
        if not url:
            url = self.gen_config.get("sd_url")
        auth = None
        sd_auth = self.gen_config.get("sd_auth", "")
        if sd_auth and ":" in sd_auth:
            auth = tuple(sd_auth.split(":", 1))
        if url:
            def send_interrupt():
                try:
                    requests.post(
                        f"{url}/sdapi/v1/interrupt",
                        timeout=5,
                        headers=DEFAULT_HEADERS,
                        auth=auth
                    )
                    print("⏹️ サーバーへ中断リクエストを送信しました。")
                except Exception as e:
                    print(f"⚠️ サーバー中断リクエスト失敗: {e}")
            threading.Thread(target=send_interrupt, daemon=True).start()
            self._log("⏳ サーバーへ中断信号を送信中...")

    def _on_generation_finished(self, success: bool, message: str, saved_dirs):
        # ProgressWorker 停止
        if hasattr(self, 'progress_worker'):
            self.progress_worker.stop()
            self.progress_worker.wait()

        was_stopped = hasattr(self, '_stop_event') and self._stop_event.is_set()
        if was_stopped:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Stopped")
            self._log("⏹️ ユーザーにより停止されました。")
            self._is_running = False
            self.btn_generate.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self._update_queue_badge()
            # 並行処理ワーカーが動いていたら強制終了
            if hasattr(self, '_parallel_worker') and self._parallel_worker:
                self._parallel_worker.finish()
            return

        if not success:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("Failed")
            self._log(f"❌ Failed: {message}")
            if hasattr(self, '_parallel_worker') and self._parallel_worker:
                # 生成失敗時もワーカーには終了を伝える
                self._parallel_worker.finish()
            else:
                self._start_next_job()
            return

        self._log(f"✅ {message}")

        # 並行処理ワーカーが有効な場合は EOF マーカーを送信し、完了を待つ
        if hasattr(self, '_parallel_worker') and self._parallel_worker:
            self._parallel_worker.finish()
            # 次のジョブ実行は _on_parallel_worker_finished で行われる
        else:
            # 並行処理が無効な場合のみ即次のジョブへ
            self._start_next_job()

    def _on_post_proc_finished(self, success: bool, message: str, worker):
        """(Deprecated) 従来の後処理ワーカー完了時のクリーンアップ。"""
        if worker in self._post_workers:
            self._post_workers.remove(worker)
        worker.deleteLater()  # QThreadの安全な遅延山除
        if success:
            self._log(f"✅ {message}")
            if not self._is_running and self._job_queue.empty():
                self.progress_bar.setValue(100)
                self.progress_bar.setFormat("All done")
        else:
            self._log(f"⚠️ Post-processing: {message}")

    def _register_post_process_thread(self, thread_id: int):
        """後処理スレッドの stdout を Post-Process Log にルーティングする。
        (threading.local 方式では start_processing() 内で設定済み。互換性のため残す)
        """
        pass

    def _on_parallel_worker_finished(self, generated: int, upscaled: int, mosaicked: int):
        """並行処理ワーカー完了時のハンドラ。"""
        self._log(f"✅ 並行処理完了: Generated={generated}, Upscaled={upscaled}, Mosaicked={mosaicked}")

        if hasattr(self, '_parallel_worker_thread') and self._parallel_worker_thread:
            self._parallel_worker_thread.quit()
            self._parallel_worker_thread.wait()
            self._parallel_worker_thread = None

        self._parallel_worker = None

        # 次のジョブを開始（後処理が完了したので）
        self._start_next_job()

        # 全て完了していればプログレスバーを100%に
        if not self._is_running and self._job_queue.empty():
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("All done")

    def _on_parallel_worker_error(self, error_type: str, error_message: str):
        """並行処理ワーカーのエラーハンドラ。"""
        self._log_post(f"❌ 並行処理エラー [{error_type}]:\n{error_message}")

    def _refresh_options(self):
        """Fetch models/samplers from API."""
        url = self.url_input.text().strip()
        if not url:
            return

        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("Refreshing...")
        
        auth = None
        sd_auth = self.gen_config.get("sd_auth", "")
        if sd_auth and ":" in sd_auth:
            auth = tuple(sd_auth.split(":", 1))
        
        self.fetch_worker = FetchOptionsWorker(url, auth=auth)
        self.fetch_worker.finished.connect(self._on_options_fetched)
        self.fetch_worker.start()

    def _on_options_fetched(self, data: dict):
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Refresh")
        
        if data.get("error"):
            self._log(f"⚠️ API Error: {data['error']}")
            QMessageBox.warning(self, "API Error", f"Failed to fetch options.\n{data['error']}")
            return

        if data["models"]:
            saved_model = self.gen_config.get("override_settings", {}).get("sd_model_checkpoint")
            
            self.combo_checkpoint.blockSignals(True)
            current = self.combo_checkpoint.currentText()
            self.combo_checkpoint.clear()
            self.combo_checkpoint.addItems(data["models"])
            
            if saved_model in data["models"]:
                self.combo_checkpoint.setCurrentText(saved_model)
            elif current in data["models"]:
                self.combo_checkpoint.setCurrentText(current)
            self.combo_checkpoint.blockSignals(False)
                
        if data["vaes"]:
            saved_vae = self.gen_config.get("override_settings", {}).get("sd_vae")
            all_vaes = ["Automatic"] + data["vaes"]
            
            self.combo_vae.blockSignals(True)
            current = self.combo_vae.currentText()
            self.combo_vae.clear()
            self.combo_vae.addItems(all_vaes) # Add Automatic
            
            if saved_vae in all_vaes:
                self.combo_vae.setCurrentText(saved_vae)
            self.combo_vae.blockSignals(False)
                
        if data["samplers"]:
            saved_sampler = self.payload.get("sampler_name")
            
            self.combo_sampler.blockSignals(True)
            current = self.combo_sampler.currentText()
            self.combo_sampler.clear()
            self.combo_sampler.addItems(data["samplers"])
            
            if saved_sampler in data["samplers"]:
                self.combo_sampler.setCurrentText(saved_sampler)
            self.combo_sampler.blockSignals(False)
        
        if data.get("schedulers"):
            saved_scheduler = self.payload.get("scheduler", "Automatic")
            all_schedulers = data["schedulers"]
            if "Automatic" not in all_schedulers:
                all_schedulers = ["Automatic"] + all_schedulers
            
            self.combo_scheduler.blockSignals(True)
            self.combo_scheduler.clear()
            self.combo_scheduler.addItems(all_schedulers)
            
            if saved_scheduler in all_schedulers:
                self.combo_scheduler.setCurrentText(saved_scheduler)
            self.combo_scheduler.blockSignals(False)
        
        self._log(f"✅ Options fetched from API. ({len(data['models'])} models)")

        # Save the working URL to config if successful
        url_used = self.url_input.text().strip()
        if url_used and url_used != self.gen_config.get("sd_url"):
            self.gen_config["sd_url"] = url_used
            self._save_config_to_disk()
            self._log(f"💾 Saved new SD URL to config: {url_used}")
            
            # Restart progress worker with new URL if needed
            if hasattr(self, 'progress_worker') and self.progress_worker.base_url != url_used.rstrip("/"):
                 self.progress_worker.stop()
                 
                 auth = None
                 sd_auth = self.gen_config.get("sd_auth", "")
                 if sd_auth and ":" in sd_auth:
                     auth = tuple(sd_auth.split(":", 1))
                     
                 self.progress_worker = ProgressWorker(url_used, auth=auth)
                 self.progress_worker.progress_signal.connect(self._update_progress)
                 self.progress_worker.start()


    def _setup_clipboard(self):
        """Monitor clipboard for Gradio URLs with debounce."""
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self._on_clipboard_change)
        
        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(500) # 500ms debounce
        self.debounce_timer.timeout.connect(self._process_clipboard)

    def _on_clipboard_change(self):
        """Trigger debounce timer on clipboard change."""
        self.debounce_timer.start()

    def _process_clipboard(self, force: bool = False):
        """Check clipboard content for valid URL.

        Args:
            force: True のとき確認ダイアログをスキップする（Pasteボタン押下時）
        """
        text = self.clipboard.text()
        if not text:
            return

        # Regex for gradio url or cloudflare url
        match = re.search(r"https?://[a-zA-Z0-9-]+\.(gradio\.live|trycloudflare\.com)", text)
        if match:
            url = match.group(0)
            if self.url_input.text() != url:
                # 自動検出時は確認ダイアログを表示する
                if not force:
                    reply = QMessageBox.question(
                        self,
                        "URL Auto-Detected",
                        f"クリップボードから新しいURLを検出しました。\n\n"
                        f"現在: {self.url_input.text() or '(未設定)'}\n"
                        f"新規: {url}\n\n"
                        f"sd_url を書き換えますか？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return

                self.url_input.setText(url)
                # Update config in memory + sd_url.json
                self.gen_config["sd_url"] = url
                self._save_sd_url_json(url)
                self._log(f"🔗 Auto-detected URL: {url}")

                # Fetch options
                self._refresh_options()

                # Visual Feedback
                original_text = self.btn_paste.text()
                self.btn_paste.setText("✨ Auto-Detected!")
                QTimer.singleShot(2000, lambda: self.btn_paste.setText(original_text))

    def _open_upscale_settings(self):
        """Open Upscale Settings Dialog."""
        cfg = self.config.get("upscale", {})
        dlg = UpscaleSettingsDialog(cfg, self)
        if dlg.exec():
            # Update Config
            new_settings = dlg.get_settings()
            self.config["upscale"].update(new_settings)
            
            # Sync config to disk
            self._update_config("upscale_settings_updated", True)
            self._log(f"✅ Upscale settings updated. (Scale: {new_settings['scale']}x)")

    def _open_mosaic_settings(self):
        """Open Full Mosaic GUI (gui.py) in Settings Mode."""
        try:
            # Launch gui.py located in the same directory as this script
            script_path = Path(__file__).parent / "gui.py"
            if script_path.exists():
                # Determine input dir (default to upscaled or raw)
                base_dir = self.config.get("output", {}).get("base_dir", "output") # Fallback
                # Better: Construct path based on ensure_dirs logic
                # However, ensure_dirs is local. Let's guess relative path "output/upscaled"
                upscaled_dir = Path(__file__).parent / "output" / "upscaled"
                
                cmd = [sys.executable, str(script_path), "--settings-mode"]
                if upscaled_dir.exists():
                    cmd.extend(["--input-dir", str(upscaled_dir.resolve())])
                
                # Explicitly pass the main config path
                cmd.extend(["--config-path", str(self._config_path.resolve())])
                
                subprocess.Popen(cmd)
                self._log(f"🚀 Launched Mosaic Tool (Settings Mode)...")
            else:
                QMessageBox.warning(self, "File Not Found", f"Could not find gui.py at:\n{script_path}")
        except Exception as e:
            self._log(f"❌ Failed to launch Mosaic GUI: {e}")
            QMessageBox.critical(self, "Launch Error", str(e))

    def _open_sfx_settings(self):
        """Open SFX Overlay Settings Dialog."""
        sfx_cfg = self.config.get("sfx", {})
        dlg = QDialog(self)
        dlg.setWindowTitle("SFX Overlay Settings")
        dlg.setMinimumWidth(320)
        layout = QFormLayout(dlg)

        # Scene
        combo_scene = QComboBox()
        scenes = ["auto", "missionary", "cowgirl", "doggy", "standing",
                  "blowjob", "paizuri", "cunnilingus", "fingering",
                  "ejaculation", "climax", "sex", "foreplay", "finish", "daily", "any"]
        combo_scene.addItems(scenes)
        combo_scene.setCurrentText(sfx_cfg.get("scene", "auto"))
        layout.addRow("Scene:", combo_scene)

        # Count
        edit_count = QLineEdit(sfx_cfg.get("count", "6-10"))
        edit_count.setPlaceholderText("e.g. 6-10")
        layout.addRow("Count (range):", edit_count)

        # Scale
        edit_scale = QLineEdit(sfx_cfg.get("scale", "0.15-0.30"))
        edit_scale.setPlaceholderText("e.g. 0.15-0.30")
        layout.addRow("Scale (range):", edit_scale)

        # OK / Cancel
        from PyQt6.QtWidgets import QDialogButtonBox
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec():
            self.config.setdefault("sfx", {})
            self.config["sfx"]["scene"] = combo_scene.currentText()
            self.config["sfx"]["count"] = edit_count.text().strip() or "6-10"
            self.config["sfx"]["scale"] = edit_scale.text().strip() or "0.15-0.30"
            self._save_config_to_disk()
            self._log(f"🔤 SFX settings updated: scene={combo_scene.currentText()}, "
                      f"count={edit_count.text()}, scale={edit_scale.text()}")

    def _open_auth_dialog(self):
        """Open Auth Settings Dialog."""
        current_auth = self.gen_config.get("sd_auth", "")
        dlg = AuthDialog(current_auth, self)
        if dlg.exec():
            new_auth = dlg.get_auth_string()
            if new_auth != current_auth:
                self.gen_config["sd_auth"] = new_auth
                self._save_config_to_disk()
                self._log(f"🔒 Auth settings updated.")

    _LOG_MAX_LINES = 500  # ログ行数上限（超過時に古い行を削除）

    def _append_to_log(self, widget: QTextEdit, message: str):
        """QTextEdit にメッセージを追加する。\r 開始なら最終行を置換、それ以外は追記。"""
        is_cr = message.startswith('\r')
        if is_cr:
            message = message.lstrip('\r')

        message = message.rstrip('\n')
        if not message.strip():
            return

        if not is_cr:
            ts = datetime.datetime.now().strftime("[%H:%M:%S]")
            message = f"{ts} {message}"

        if is_cr:
            # 最終行を置換（tqdm 等のインライン更新）
            cursor = widget.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.insertText(message)
        else:
            widget.append(message)

        # 行数制限
        doc = widget.document()
        if doc.blockCount() > self._LOG_MAX_LINES:
            cursor = widget.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            # 超過分を選択して削除
            excess = doc.blockCount() - self._LOG_MAX_LINES
            for _ in range(excess):
                cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # 残った空行を削除

    def _log(self, message: str):
        """Generation Log タブにメッセージを出力する（デフォルト）。"""
        self._append_to_log(self.txt_log_gen, message)

    def _log_gen(self, message: str):
        """Generation Log タブにメッセージを出力する。"""
        self._append_to_log(self.txt_log_gen, message)

    def _log_post(self, message: str):
        """Post-Process Log タブにメッセージを出力する。"""
        self._append_to_log(self.txt_log_post, message)


    def _load_config(self) -> dict:
        import copy
        
        # 1. デフォルト設定をロード
        default_cfg = {}
        if DEFAULT_CONFIG_PATH.exists():
            try:
                with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
                    default_cfg = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"Error loading default config: {e}")

        # もし対象がDEFAULT_CONFIG_PATHなら、そのまま返す
        if self._config_path == DEFAULT_CONFIG_PATH:
            return default_cfg

        # 2. カスタム設定が存在しない場合はデフォルトをごっそりコピー
        if not self._config_path.exists() and default_cfg:
            import shutil
            try:
                shutil.copy2(DEFAULT_CONFIG_PATH, self._config_path)
                print(f"ℹ️ Initialization: Created new config profile '{self._config_path.name}' and copied defaults.")
                return copy.deepcopy(default_cfg)
            except Exception as e:
                print(f"Failed to copy default config: {e}")

        # 3. カスタム設定が存在する場合はディープマージ
        user_cfg = {}
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    user_cfg = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"Error loading custom config: {e}")

        def deep_merge(dict_base, dict_update):
            for k, v in dict_update.items():
                if isinstance(v, dict) and k in dict_base and isinstance(dict_base[k], dict):
                    deep_merge(dict_base[k], v)
                else:
                    if k not in dict_base:
                        dict_base[k] = v
            return dict_base

        # user_cfg に対して default_cfg をベースとして足りない要素を補完
        merged_cfg = deep_merge(user_cfg, copy.deepcopy(default_cfg))
        return merged_cfg

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        
        # Reduced default height for better fit on 1080p screens
        self.resize(1200, 860)
        self.setMinimumSize(900, 700)
        
        # 1. URL Section
        url_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste Gradio/Cloudflare URL here...")
        self.btn_paste = QPushButton("Paste / Auto Detect")
        self.btn_paste.clicked.connect(lambda: self._process_clipboard(force=True))
        
        self.btn_auth = QPushButton("🔒")
        self.btn_auth.setFixedWidth(40)
        self.btn_auth.setToolTip("Set Basic Auth (User:Pass)")
        self.btn_auth.clicked.connect(self._open_auth_dialog)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setFixedWidth(80)
        self.btn_refresh.clicked.connect(self._refresh_options)
        
        url_layout.addWidget(QLabel("SD URL:"))
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.btn_paste)
        url_layout.addWidget(self.btn_auth)
        url_layout.addWidget(self.btn_refresh)
        main_layout.addLayout(url_layout)
        
        # 2. Main Content (Settings + Prompts)
        content_layout = QHBoxLayout()
        
        # Left Panel: Settings
        settings_group = QGroupBox("Generation Settings")
        settings_layout = QFormLayout(settings_group)
        
        self.combo_checkpoint = QComboBox() # Dynamic
        self.combo_vae = QComboBox() # Dynamic
        self.combo_sampler = QComboBox() # Dynamic
        self.combo_sampler.addItems(["Euler a", "Euler", "DPM++ 2M Karras", "DPM++ SDE Karras", "DDIM"]) # Fallback
        
        self.combo_scheduler = QComboBox() # Dynamic
        self.combo_scheduler.addItems(["Automatic", "Uniform", "Karras", "Exponential", "Polyexponential", "SGM Uniform"]) # Fallback
        
        self.spin_steps = QSpinBox()
        self.spin_steps.setRange(1, 150)
        
        self.spin_width = QSpinBox()
        self.spin_width.setRange(64, 2048)
        self.spin_width.setSingleStep(64)
        
        self.spin_height = QSpinBox()
        self.spin_height.setRange(64, 2048)
        self.spin_height.setSingleStep(64)
        
        wh_layout = QHBoxLayout()
        wh_layout.addWidget(QLabel("W:"))
        wh_layout.addWidget(self.spin_width)
        wh_layout.addWidget(QLabel("H:"))
        wh_layout.addWidget(self.spin_height)
        
        self.spin_cfg = QDoubleSpinBox()
        self.spin_cfg.setRange(1.0, 30.0)
        self.spin_cfg.setSingleStep(0.5)
        
        self.spin_batch_count = QSpinBox()
        self.spin_batch_count.setRange(1, 100)
        
        self.spin_batch_size = QSpinBox()
        self.spin_batch_size.setRange(1, 8)
        
        batch_layout = QHBoxLayout()
        batch_layout.addWidget(QLabel("Size:"))
        batch_layout.addWidget(self.spin_batch_size)
        batch_layout.addWidget(QLabel("Count:"))
        batch_layout.addWidget(self.spin_batch_count)
        
        self.spin_seed = QSpinBox()
        self.spin_seed.setRange(-1, 2147483647)
        self.spin_seed.setValue(-1)
        self.spin_seed.setSpecialValueText("Random (-1)")
        
        settings_layout.addRow("Checkpoint:", self.combo_checkpoint)
        settings_layout.addRow("VAE:", self.combo_vae)
        settings_layout.addRow("Sampler:", self.combo_sampler)
        settings_layout.addRow("Schedule type:", self.combo_scheduler)
        settings_layout.addRow("Steps:", self.spin_steps)
        settings_layout.addRow("Resolution:", wh_layout) # Combined
        settings_layout.addRow("CFG Scale:", self.spin_cfg)
        settings_layout.addRow("Batch:", batch_layout) # Combined
        settings_layout.addRow("Seed:", self.spin_seed)
        
        # Debug / Extras
        self.chk_skip_gen = QCheckBox("Skip Generation (Post-Process Only)")
        self.chk_skip_gen.setToolTip("Run Upscale/Mosaic on existing images without generating new ones.")
        settings_layout.addRow(self.chk_skip_gen)
        
        # Left Panel (Bottom): Post-Processing Group
        post_group = QGroupBox("Post-Processing")
        post_layout = QVBoxLayout(post_group)
        
        # Upscale Row
        row_upscale = QHBoxLayout()
        self.chk_upscale = QCheckBox("Enable Upscale")
        self.btn_upscale_settings = QPushButton("⚙️")
        self.btn_upscale_settings.setFixedWidth(30)
        self.btn_upscale_settings.clicked.connect(self._open_upscale_settings)
        row_upscale.addWidget(self.chk_upscale)
        row_upscale.addWidget(self.btn_upscale_settings)
        row_upscale.addStretch()
        
        # Mosaic Row
        row_mosaic = QHBoxLayout()
        self.chk_mosaic = QCheckBox("Enable Auto Mosaic")
        self.btn_mosaic_settings = QPushButton("⚙️")
        self.btn_mosaic_settings.setFixedWidth(30)
        self.btn_mosaic_settings.clicked.connect(self._open_mosaic_settings)
        row_mosaic.addWidget(self.chk_mosaic)
        row_mosaic.addWidget(self.btn_mosaic_settings)
        row_mosaic.addStretch()

        # SFX Overlay Row
        row_sfx = QHBoxLayout()
        self.chk_sfx = QCheckBox("Enable SFX Overlay")
        self.btn_sfx_settings = QPushButton("⚙️")
        self.btn_sfx_settings.setFixedWidth(30)
        self.btn_sfx_settings.clicked.connect(self._open_sfx_settings)
        row_sfx.addWidget(self.chk_sfx)
        row_sfx.addWidget(self.btn_sfx_settings)
        row_sfx.addStretch()

        # Limb Check Row
        row_limb = QHBoxLayout()
        self.chk_limb_check = QCheckBox("Enable Limb Check")
        self.chk_limb_check.setToolTip(
            "Vision LLM で四肢破綻・体形異常を検出し NG 画像を自動排除します\n"
            "（1枚あたり約6秒、vision_url.json に VLM URL が必要）"
        )
        row_limb.addWidget(self.chk_limb_check)
        row_limb.addStretch()

        post_layout.setSpacing(4)
        post_layout.setContentsMargins(8, 6, 8, 6)
        post_layout.addLayout(row_upscale)
        post_layout.addLayout(row_mosaic)
        post_layout.addLayout(row_sfx)
        post_layout.addLayout(row_limb)
        post_group.setMaximumHeight(175)  # 4行分
        # left_container には settings_group のみ
        # post_group はボタン行の左側に並べるため、ここでは追加しない
        left_container = QWidget()
        left_container_layout = QVBoxLayout(left_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.addWidget(settings_group)
        left_container_layout.addStretch()

        from PyQt6.QtWidgets import QScrollArea
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_container)
        left_scroll.setMinimumWidth(300)
        left_scroll.setMaximumWidth(420)  # フォームのラベル+フィールドが切れない幅に拡大

        content_layout.addWidget(left_scroll, stretch=1)
        
        # Right Panel: Prompts
        prompts_layout = QVBoxLayout()

        self.txt_prompt = QTextEdit()
        self.txt_prompt.setAcceptRichText(False)
        self.txt_prompt.setPlaceholderText("Positive Prompt")
        self.txt_prompt.setFixedHeight(175)  # アップデート前+1.5cm相当（1.5cm≈ 57px @ 96dpi）

        self.txt_neg_prompt = QTextEdit()
        self.txt_neg_prompt.setAcceptRichText(False)
        self.txt_neg_prompt.setPlaceholderText("Negative Prompt")
        self.txt_neg_prompt.setFixedHeight(60)  # ネガティブプロンプトは小さく固定

        prompts_layout.addWidget(QLabel("Positive Prompt:"))
        prompts_layout.addWidget(self.txt_prompt)
        prompts_layout.addWidget(QLabel("Negative Prompt:"))
        prompts_layout.addWidget(self.txt_neg_prompt)
        prompts_layout.addStretch()

        content_layout.addLayout(prompts_layout, stretch=2)
        
        main_layout.addLayout(content_layout)

        # 3. Middle Row: [POST-PROCESSING (左=設定パネル幅)] | [Generate + Stop (右=プロンプト幅)]
        # post_group の maxHeight を外して自然に伸ばす
        post_group.setMaximumHeight(16777215)  # 제限解除
        mid_row = QHBoxLayout()
        mid_row.addWidget(post_group, stretch=1)  # 左パネル(stretch=1)と同幅

        self.btn_generate = QPushButton("▶ Generate (+ Pipeline)")
        self.btn_generate.setFixedHeight(50)
        self.btn_generate.setProperty('class', 'success')

        self.btn_stop = QPushButton("🛑 Stop")
        self.btn_stop.setFixedHeight(50)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setProperty('class', 'danger')

        # Queue badge: キュー待機数を表示
        self.lbl_queue = QLabel("")
        self.lbl_queue.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_generate, stretch=2)
        btn_row.addWidget(self.lbl_queue)
        btn_row.addWidget(self.btn_stop, stretch=1)

        # Progress Bar
        from PyQt6.QtWidgets import QProgressBar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        btn_col = QVBoxLayout()
        btn_col.addLayout(btn_row)
        btn_col.addWidget(self.progress_bar)

        mid_row.addLayout(btn_col, stretch=2)  # 右パネル(stretch=2)と同幅
        main_layout.addLayout(mid_row)
        
        # ── Log Tabs ──
        self.log_tabs = QTabWidget()
        self.log_tabs.setFixedHeight(210)

        log_font = "Consolas, 'Courier New', monospace"

        self.txt_log_gen = QTextEdit()
        self.txt_log_gen.setReadOnly(True)
        self.txt_log_gen.setStyleSheet(f"font-family: {log_font}; font-size: 12px;")
        self.log_tabs.addTab(self.txt_log_gen, "Generation Log")

        self.txt_log_post = QTextEdit()
        self.txt_log_post.setReadOnly(True)
        self.txt_log_post.setStyleSheet(f"font-family: {log_font}; font-size: 12px;")
        self.log_tabs.addTab(self.txt_log_post, "Post-Process Log")

        main_layout.addWidget(self.log_tabs)

    def _load_values(self):
        # Apply initial config values — sd_url.json があればそちらを優先
        import json as _json
        sd_url_path = Path(__file__).resolve().parent.parent / "sd_url.json"
        startup_url = self.gen_config.get("sd_url", "")
        try:
            if sd_url_path.exists():
                data = _json.loads(sd_url_path.read_text(encoding="utf-8"))
                file_url = data.get("url", "")
                if file_url:
                    startup_url = file_url
        except Exception:
            pass
        self.gen_config["sd_url"] = startup_url
        self.url_input.setText(startup_url)
        self.spin_steps.setValue(self.payload.get("steps", 20))
        self.spin_width.setValue(self.payload.get("width", 512))
        self.spin_height.setValue(self.payload.get("height", 512))
        self.spin_cfg.setValue(self.payload.get("cfg_scale", 7.0))
        
        # Prompts
        self.txt_prompt.setPlainText(self.payload.get("prompt", ""))
        self.txt_neg_prompt.setPlainText(self.payload.get("negative_prompt", ""))
        
        # Batch (Default 1 if missing)
        self.spin_batch_count.setValue(self.config.get("_gui_batch_count", 1))
        self.spin_batch_size.setValue(self.payload.get("batch_size", 1))
        self.spin_seed.setValue(self.payload.get("seed", -1))
        
        # Scheduler
        saved_scheduler = self.payload.get("scheduler", "Automatic")
        idx = self.combo_scheduler.findText(saved_scheduler)
        if idx >= 0:
            self.combo_scheduler.setCurrentIndex(idx)
        
        # Post-Processing
        self.chk_upscale.setChecked(self.config.get("upscale", {}).get("enabled", False))
        self.chk_mosaic.setChecked(self.config.get("mosaic", {}).get("enabled", False))
        self.chk_sfx.setChecked(self.config.get("sfx", {}).get("enabled", False))
        self.chk_limb_check.setChecked(self.config.get("limb_check", {}).get("enabled", False))

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Workflow-Gravity Generation GUI")
    parser.add_argument("--config", "-c", default=None, help="設定ファイルのパス (default: config.yaml)")
    args, remaining = parser.parse_known_args()
    config_path = Path(args.config) if args.config else None

    app = QApplication(remaining)

    # Enable High DPI scaling
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    # Use 'dark_cyan.xml' for better contrast/visibility
    apply_stylesheet(app, theme='dark_cyan.xml')
    
    # Custom CSS tweaks for improved visibility
    # Ensuring dark background for inputs and white text
    # Aggressively override focus states to prevent "cyan background" from theme
    custom_css = """
    /* Global Input Styling */
    QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        font-size: 14px;
        color: #ffffff !important;
        background-color: #000000 !important;
        border: 1px solid #78909c;
        border-radius: 4px;
        padding: 4px;
        selection-background-color: #546e7a; /* Darker Blue-Grey selection */
        selection-color: #ffffff;
    }
    
    /* Focus States - Prevent Cyan Background */
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
        background-color: #000000 !important; 
        color: #ffffff !important;
        border: 2px solid #26a69a; /* Cyan border only */
        selection-background-color: #546e7a;
    }

    /* ComboBox Dropdown & Items */
    QComboBox QAbstractItemView {
        background-color: #263238; /* Dark Menu Background */
        color: #ffffff;
        selection-background-color: #546e7a; /* Dark Selection */
        selection-color: #ffffff;
        outline: none;
    }

    /* Fix strict cyan in some qt-material themes for selected text */
    * {
        selection-background-color: #546e7a;
        selection-color: #ffffff;
    }

    /* Labels & Groups */
    QLabel, QGroupBox, QCheckBox {
        font-size: 14px;
        color: #ffffff;
        font-weight: bold;
    }
    QGroupBox::title {
        color: #ffffff;
    }
    QCheckBox::indicator {
        width: 18px;
        height: 18px;
        border: 1px solid #78909c;
        background: #000000;
        border-radius: 3px;
    }
    QCheckBox::indicator:checked {
        background: #26a69a;
        border: 1px solid #26a69a;
    }

    /* Progress Bar */
    QProgressBar {
        border: 2px solid #546e7a;
        border-radius: 5px;
        text-align: center;
        color: #ffffff;
        background-color: #263238;
    }
    QProgressBar::chunk {
        background-color: #26a69a;
    }

    /* Buttons */
    QPushButton {
        font-weight: bold;
        padding: 8px;
        color: #ffffff !important;
    }
    """
    app.setStyleSheet(app.styleSheet() + custom_css)
    
    window = GenerationWindow(config_path=config_path)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
