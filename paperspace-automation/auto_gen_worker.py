"""
Queue-file ベースの自動生成ワーカー。

/storage/paperspace-automation/queue.txt の各行を順に WebUI API に投げ、
checkpoint.json で進捗を保存する。
再起動後は checkpoint の次の行から再開する。

queue.txt フォーマット（workflow-gravity の prompts.txt と同じ形式）:
  --prompt "..." --batch_size 2 --ad_steps 40 --outpath_samples "outputs/..." --negative_prompt "..." --ad_prompt "..."
  # で始まる行と空行はスキップ

共通パラメータは /notebooks/queue_config.json で上書き可能（省略時はデフォルト値を使用）。
"""

import hashlib
import json
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

import requests

WEBUI_API = "http://127.0.0.1:7860"
STORAGE = Path("/storage/paperspace-automation")
QUEUE_FILE = STORAGE / "queue.txt"
CHECKPOINT_FILE = STORAGE / "checkpoint.json"
CONFIG_FILE = Path("/notebooks/queue_config.json")
WEBUI_ROOT = Path("/notebooks/stable-diffusion-webui")
DONE_FLAG = STORAGE / "DONE"

DEFAULT_CONFIG = {
    "steps": 28,
    "cfg_scale": 7.0,
    "width": 832,
    "height": 1216,
    "sampler_name": "DPM++ 2M",
    "scheduler": "Karras",
    "seed": -1,
    "per_image_timeout": 600,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
            print(f"✅ queue_config.json 読み込み: {cfg}")
        except Exception as e:
            print(f"[WARN] queue_config.json 読み込み失敗: {e}")
    return cfg


def load_queue() -> list:
    if not QUEUE_FILE.exists():
        raise FileNotFoundError(f"{QUEUE_FILE} が見つかりません。Paperspace にアップロードしてください。")
    lines = []
    for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    print(f"✅ queue.txt 読み込み: {len(lines)} 件")
    return lines


def load_checkpoint() -> int:
    if CHECKPOINT_FILE.exists():
        try:
            return int(json.loads(CHECKPOINT_FILE.read_text()).get("last_done", -1))
        except Exception:
            pass
    return -1


def save_checkpoint(index: int):
    tmp = CHECKPOINT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"last_done": index}))
    shutil.move(str(tmp), str(CHECKPOINT_FILE))


def write_done_flag(queue_hash: str):
    import os
    import tempfile
    data = json.dumps({
        "queue_hash": queue_hash,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    fd, tmp = tempfile.mkstemp(dir=STORAGE)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, DONE_FLAG)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    print(f"DONE フラグ書き出し: {DONE_FLAG}")


def parse_prompt_line(line: str) -> dict:
    """--prompt "..." --key value 形式を辞書にパース。"""
    if "--prompt" not in line and "--negative_prompt" not in line:
        return {"prompt": line}
    try:
        args = shlex.split(line)
        parsed = {}
        current_key = None
        for token in args:
            if token.startswith("--"):
                current_key = token.lstrip("-")
            elif current_key:
                parsed[current_key] = token
                current_key = None
        return parsed
    except Exception as e:
        print(f"[WARN] パース失敗: {e} → 生の文字列として扱います")
        return {"prompt": line}


def build_payload(parsed: dict, cfg: dict) -> dict:
    payload = dict(cfg)

    payload["prompt"] = re.sub(r"\s+", " ", parsed.get("prompt", "")).strip()

    if "negative_prompt" in parsed:
        payload["negative_prompt"] = re.sub(r"\s+", " ", parsed["negative_prompt"]).strip()

    if "batch_size" in parsed:
        try:
            payload["batch_size"] = int(parsed["batch_size"])
        except ValueError:
            print(f"[WARN] batch_size 無効: {parsed['batch_size']}")

    payload["save_images"] = True

    # --outpath_samples → WebUI の保存先を override
    if "outpath_samples" in parsed:
        out = parsed["outpath_samples"].strip().strip("\"'")
        if not Path(out).is_absolute():
            out = str(WEBUI_ROOT / out)
        payload["override_settings"] = {"outdir_txt2img_samples": out}
        payload["override_settings_restore_afterwards"] = True

    # ADetailer（--ad_prompt / --ad_steps）
    ad_prompt = parsed.get("ad_prompt")
    ad_steps = parsed.get("ad_steps")
    if ad_prompt or ad_steps:
        ad_arg = {"ad_model": "face_yolov8n.pt"}
        if ad_prompt:
            ad_arg["ad_prompt"] = ad_prompt.strip().strip("\"'")
        if ad_steps:
            try:
                ad_arg["ad_steps"] = int(ad_steps)
                ad_arg["ad_use_steps"] = True
            except ValueError:
                print(f"[WARN] ad_steps 無効: {ad_steps}")
        payload["alwayson_scripts"] = {
            "ADetailer": {"args": [True, False, ad_arg]}
        }

    return payload


def wait_webui(timeout: int = 600):
    """WebUI が応答するまで待機（最大 timeout 秒）。"""
    print("WebUI の起動を待機中...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{WEBUI_API}/sdapi/v1/sd-models", timeout=5)
            if r.ok and isinstance(r.json(), list) and r.json():
                print("✅ WebUI 準備完了")
                return
        except Exception:
            pass
        time.sleep(10)
    raise RuntimeError(f"WebUI が {timeout}秒経っても応答しません")


def generate(payload: dict, index: int, timeout: int = 600) -> bool:
    try:
        r = requests.post(f"{WEBUI_API}/sdapi/v1/txt2img", json=payload, timeout=timeout)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  [{index}] ERROR: {e}")
        return False


def main():
    print("=== auto_gen_worker 起動 ===")
    wait_webui()

    cfg = load_config()
    queue = load_queue()
    queue_hash = hashlib.md5(QUEUE_FILE.read_bytes()).hexdigest()
    last_done = load_checkpoint()
    start_from = last_done + 1

    total = len(queue)
    remaining = total - start_from
    print(f"全 {total} 件 / 開始: {start_from} 番目（残り {remaining} 件）")

    if start_from >= total:
        print("全プロンプト処理済みです。queue.txt を更新してください。")
        write_done_flag(queue_hash)
        sys.exit(0)

    for i in range(start_from, total):
        line = queue[i]
        parsed = parse_prompt_line(line)
        payload = build_payload(parsed, cfg)

        preview = parsed.get("prompt", "")[:60]
        print(f"[{i}/{total-1}] {preview}...")

        ok = generate(payload, i, timeout=cfg.get("per_image_timeout", 600))
        if ok:
            save_checkpoint(i)
            print(f"  [{i}] ✅ 完了 (checkpoint 保存)")
        else:
            print(f"  [{i}] ❌ 失敗 → スキップして続行")

    print("=== 全プロンプト完了 ===")
    write_done_flag(queue_hash)


if __name__ == "__main__":
    main()
