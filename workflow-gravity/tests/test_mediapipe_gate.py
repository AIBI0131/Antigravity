"""
Phase 1 Unit Tests — MediaPipe AND gate
========================================
VLM は不要。gate ロジック単体を検証する。

実行方法:
  cd i:/マイドライブ/Antigravity/workflow-gravity
  python -m pytest tests/test_mediapipe_gate.py -v
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image as _PIL

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.vision_limb_checker import LimbCheckResult, VisionLimbChecker


# ──────────────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────────────
def _make_checker() -> VisionLimbChecker:
    """_init_client() をモックして VLM 接続なしで checker を生成する。"""
    with patch.object(VisionLimbChecker, "_init_client", return_value=(MagicMock(), "mock-model")):
        return VisionLimbChecker()


def _make_temp_png() -> str:
    """10x10 の白いPNGを一時ファイルに書き出してパスを返す。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img = _PIL.fromarray(np.ones((10, 10, 3), dtype=np.uint8) * 200)
    img.save(tmp.name)
    return tmp.name


# ──────────────────────────────────────────────────────────────
# テスト1: VLM=NG(IB) + MediaPipe=OK → 降格
# ──────────────────────────────────────────────────────────────
def test_gate_ib_mediapipe_ok_downgrades():
    """IB issue のみ → MediaPipe OK → ok=True に降格"""
    checker = _make_checker()
    checker._mp_checker = MagicMock()
    checker._mp_checker.check.return_value = (True, [], {})  # MediaPipe OK

    result = LimbCheckResult(
        path="dummy.png",
        ok=False,
        issues=["IMPOSSIBLE BEND: knee hyperextended 180 degrees"],
        confidence=0.85,
    )
    out = checker._apply_gate_verifiers(result, _make_temp_png())

    assert out.ok is True, f"Expected ok=True, got {out.ok}, issues={out.issues}"
    assert out.issues == []


# ──────────────────────────────────────────────────────────────
# テスト2: VLM=NG(WRONG_GENITALS) → gate 対象外 → NG 維持
# ──────────────────────────────────────────────────────────────
def test_gate_non_ib_category_unchanged():
    """IB/BACKWARD_LEG 以外のカテゴリ → gate は発動しない"""
    checker = _make_checker()

    result = LimbCheckResult(
        path="dummy.png",
        ok=False,
        issues=["WRONG GENITALS: shaft visible at female groin"],
        confidence=0.85,
    )
    out = checker._apply_gate_verifiers(result, "dummy.png")

    assert out.ok is False
    assert "WRONG GENITALS" in out.issues[0]
    assert checker._mp_checker is None  # MediaPipe がロードされていない


# ──────────────────────────────────────────────────────────────
# テスト3: VLM=NG(IB + BODY_FUSION) → IB 除去後も BODY_FUSION で NG
# ──────────────────────────────────────────────────────────────
def test_gate_ib_removed_other_issue_remains():
    """IB + 他カテゴリの複合 → IB を除去後、他 issue で NG 維持"""
    checker = _make_checker()
    checker._mp_checker = MagicMock()
    checker._mp_checker.check.return_value = (True, [], {})  # MediaPipe OK

    result = LimbCheckResult(
        path="dummy.png",
        ok=False,
        issues=[
            "IMPOSSIBLE BEND: knee hyperextended",
            "BODY FUSION: two characters merge",
        ],
        confidence=0.85,
    )
    out = checker._apply_gate_verifiers(result, _make_temp_png())

    assert out.ok is False
    assert len(out.issues) == 1
    assert "BODY FUSION" in out.issues[0]


# ──────────────────────────────────────────────────────────────
# テスト4: MediaPipe ロード失敗 → fail-open (NG 維持)
# ──────────────────────────────────────────────────────────────
def test_gate_mediapipe_load_failure_fail_open():
    """MediaPipe ロード失敗 → fail-open で VLM NG を維持"""
    checker = _make_checker()
    checker._mp_load_failed = True

    result = LimbCheckResult(
        path="dummy.png",
        ok=False,
        issues=["IMPOSSIBLE BEND: extreme hyperextension"],
        confidence=0.85,
    )
    out = checker._apply_gate_verifiers(result, "dummy.png")

    assert out.ok is False
    assert "IMPOSSIBLE BEND" in out.issues[0]


# ──────────────────────────────────────────────────────────────
# テスト5: VLM=OK → gate は呼ばれず OK 維持
# ──────────────────────────────────────────────────────────────
def test_gate_vlm_ok_unchanged():
    """VLM=OK の場合は gate を適用しない"""
    checker = _make_checker()

    result = LimbCheckResult(path="dummy.png", ok=True, issues=[], confidence=0.95)
    out = checker._apply_gate_verifiers(result, "dummy.png")

    assert out.ok is True
    assert checker._mp_checker is None  # ロードされていない


# ──────────────────────────────────────────────────────────────
# テスト6: BACKWARD LEG issue → gate 対象に含まれる
# ──────────────────────────────────────────────────────────────
def test_gate_backward_leg_also_gated():
    """BACKWARD LEG も gate 対象"""
    checker = _make_checker()
    checker._mp_checker = MagicMock()
    checker._mp_checker.check.return_value = (True, [], {})

    result = LimbCheckResult(
        path="dummy.png",
        ok=False,
        issues=["BACKWARD LEG: leg extends behind spine plane"],
        confidence=0.8,
    )
    out = checker._apply_gate_verifiers(result, _make_temp_png())

    assert out.ok is True


# ──────────────────────────────────────────────────────────────
# テスト7: MediaPipe も NG → VLM NG を維持
# ──────────────────────────────────────────────────────────────
def test_gate_mediapipe_also_ng_keeps_vlm_ng():
    """MediaPipe も NG の場合 → VLM の NG を維持"""
    checker = _make_checker()
    checker._mp_checker = MagicMock()
    checker._mp_checker.check.return_value = (False, ["both knees > 177 deg"], {})

    result = LimbCheckResult(
        path="dummy.png",
        ok=False,
        issues=["IMPOSSIBLE BEND: knee hyperextended"],
        confidence=0.9,
    )
    out = checker._apply_gate_verifiers(result, _make_temp_png())

    assert out.ok is False
    assert "IMPOSSIBLE BEND" in out.issues[0]
