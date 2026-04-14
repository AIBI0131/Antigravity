"""
Phase 0 Smoke Test — ハイブリッド精度向上 事前測定
=====================================================

検証内容:
  1. MediaPipeChecker: 現 FP=5 画像に対してドライラン
     - 5枚のうち何枚が MediaPipe=OK と判定されるか（= AND gate で正しく降格できる）
     - 期待: IB/BACKWARD_LEG の 3枚が OK と判定 → Phase 1 で FP削減可能

  2. DWPose 人体検出分布: TP(NGimage/)とFP(test_limb_100/)で1体 vs 2体 分布
     - Phase 2 前提「通常性交=2体検出、futanari/solo=1体検出」の検証
     - OK 側でも 1体が多ければ Phase 2 は再設計が必要

  3. レイテンシ計測: MediaPipe 1枚あたりの処理時間

使用方法:
  cd i:/マイドライブ/Antigravity/workflow-gravity
  python phase0_smoke_test.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import cv2
import numpy as np
from PIL import Image


def imread_unicode(path: Path) -> np.ndarray | None:
    """日本語パス対応 imread（PIL 経由）。"""
    try:
        with Image.open(str(path)) as img:
            img_rgb = img.convert("RGB")
            img_rgb.load()
        return cv2.cvtColor(np.array(img_rgb), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


ROOT = Path(__file__).parent

# ─────────────────────────────────────────────────────────
# パス定義
# ─────────────────────────────────────────────────────────
FP_IMAGES = [
    ROOT / "output/test_limb_100/00167-402633260.png",
    ROOT / "output/test_limb_100/00179-1510849899.png",
    ROOT / "output/test_limb_100/00215-4116031628.png",
    ROOT / "output/test_limb_100/00227-4278962859.png",
    ROOT / "output/test_limb_100/00269-1481660237.png",
]

NG_IMAGE_DIR = ROOT / "NGimage"          # TP セット (44枚)
OK_IMAGE_DIR = ROOT / "output/test_limb_100"  # FP/OK セット (94枚)


# ─────────────────────────────────────────────────────────
# 1. MediaPipe smoke test on FP=5 images
# ─────────────────────────────────────────────────────────
def test_mediapipe_on_fp_images():
    print("=" * 60)
    print("1. MediaPipeChecker — FP=5 画像ドライラン")
    print("   目的: VLM-NG だが MediaPipe-OK → AND gate で FP を降格できるか確認")
    print("=" * 60)

    try:
        from modules.mediapipe_utils import MediaPipeChecker
        checker = MediaPipeChecker()
        print("  ✅ MediaPipeChecker ロード成功\n")
    except FileNotFoundError as e:
        print(f"  ❌ モデルファイル未検出: {e}\n")
        return
    except ImportError as e:
        print(f"  ❌ mediapipe 未インストール: {e}\n")
        return

    # VLM が検出したカテゴリ（前回の分析結果から）
    fp_categories = {
        "00167": "IMPOSSIBLE_BEND",
        "00179": "IMPOSSIBLE_BEND",
        "00215": "IMPOSSIBLE_BEND",
        "00227": "BODY_FUSION",
        "00269": "WRONG_GENITALS",
    }

    mp_results = {}
    latencies = []

    for img_path in FP_IMAGES:
        if not img_path.exists():
            print(f"  ⚠️  画像なし: {img_path.name}")
            continue

        img_bgr = imread_unicode(img_path)
        if img_bgr is None:
            print(f"  ⚠️  読み込み失敗: {img_path.name}")
            continue

        t0 = time.time()
        ok, issues, meta = checker.check(img_bgr)
        elapsed = time.time() - t0
        latencies.append(elapsed)

        stem = img_path.stem[:5]
        category = fp_categories.get(stem, "?")
        verdict = "OK" if ok else f"NG {issues}"
        gate_result = "AND-gate: 降格→OK ✅" if ok else "AND-gate: NG維持 ⚠️"
        print(f"  {img_path.name}  VLM={category}  MediaPipe={verdict}")
        print(f"    {gate_result}  ({elapsed:.2f}s)")
        mp_results[img_path.name] = ok

    ok_count = sum(1 for v in mp_results.values() if v)
    total = len(mp_results)
    print(f"\n  結果: {ok_count}/{total} 枚が MediaPipe=OK → FP {total - ok_count}件が残留, {ok_count}件削減見込み")

    if latencies:
        avg = sum(latencies) / len(latencies)
        print(f"  レイテンシ平均: {avg:.2f}s/枚")
    print()


# ─────────────────────────────────────────────────────────
# 2. DWPose 人体検出分布
# ─────────────────────────────────────────────────────────
def test_dwpose_person_count():
    print("=" * 60)
    print("2. DWPose 人体検出数分布 — TP(NG画像) vs OK画像")
    print("   目的: futanari=1体, 通常性交=2体 の分離度を確認")
    print("=" * 60)

    try:
        from modules.dwpose_utils import DWPoseChecker
        dw = DWPoseChecker(device="cpu")
        print("  ✅ DWPoseUtils ロード成功\n")
    except FileNotFoundError as e:
        print(f"  ❌ ONNXモデル未検出: {e}")
        print("     (modules/ に yolox_l.onnx / dw-ll_ucoco_384.onnx が必要)")
        print("     → DWPose smoke test をスキップ。Phase 0 は MediaPipe のみで評価\n")
        return
    except Exception as e:
        print(f"  ❌ DWPose ロードエラー: {e}\n")
        return

    def count_persons_in_dir(img_dir: Path, label: str, max_images: int = 50):
        imgs = sorted(img_dir.glob("*.png"))[:max_images]
        counts = {0: 0, 1: 0, 2: 0}

        print(f"  [{label}] {len(imgs)} 枚を処理中...")
        for img_path in imgs:
            img_bgr = imread_unicode(img_path)
            if img_bgr is None:
                continue
            # _detect_persons(max_det=2) で人数カウント
            boxes = dw._detect_persons(img_bgr, max_det=2)
            n = min(len(boxes), 2)
            counts[n] = counts.get(n, 0) + 1

        total = sum(counts.values())
        print(f"    0体: {counts.get(0,0)}枚 ({counts.get(0,0)/total*100:.0f}%)")
        print(f"    1体: {counts.get(1,0)}枚 ({counts.get(1,0)/total*100:.0f}%)")
        print(f"    2体: {counts.get(2,0)}枚 ({counts.get(2,0)/total*100:.0f}%)")
        return counts

    ng_counts = count_persons_in_dir(NG_IMAGE_DIR, "TP(NG画像) N=44")
    print()
    ok_counts = count_persons_in_dir(OK_IMAGE_DIR, "OK画像  N=94")

    print("\n  ── Phase 2 前提検証 ──")
    ng_1body_pct = ng_counts.get(1, 0) / max(sum(ng_counts.values()), 1) * 100
    ok_1body_pct = ok_counts.get(1, 0) / max(sum(ok_counts.values()), 1) * 100
    print(f"  TP(NG) 1体のみ率: {ng_1body_pct:.0f}%  (futanari/solo の割合)")
    print(f"  OK     1体のみ率: {ok_1body_pct:.0f}%  (この値が高いと AND 条件が機能しない)")

    if ok_1body_pct >= 40:
        print("  ⚠️  OK 側でも 1体のみが多い → Phase 2 の AND 条件「2体なし = futanari」は")
        print("     信頼度が低い。Phase 2 は見送りまたは条件再設計を推奨。")
    else:
        print("  ✅ OK 側の 1体率が低い → Phase 2 の AND 条件は有効と判断。")
    print()


# ─────────────────────────────────────────────────────────
# 3. レイテンシサマリ（MediaPipe からフィードバック済み）
# ─────────────────────────────────────────────────────────
def print_latency_summary():
    print("=" * 60)
    print("3. レイテンシ予測サマリ")
    print("=" * 60)
    print("  現行 VLM limb check: ~6秒/枚")
    print("  Phase 1 追加コスト (VLM-NG 時のみ MediaPipe): +1-2秒/NG枚")
    print("  Phase 2 追加コスト (VLM-OK 時のみ NudeNet CPU): +1-2秒/OK枚")
    print("  Phase 3 追加コスト (VLM-NG 時のみ DWPose CPU): +2-3秒/NG枚")
    print("  NG 率が低ければ全体の追加コストは限定的。")
    print()


# ─────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🔬 Phase 0 Smoke Test 開始\n")
    test_mediapipe_on_fp_images()
    test_dwpose_person_count()
    print_latency_summary()
    print("🏁 Phase 0 完了")
