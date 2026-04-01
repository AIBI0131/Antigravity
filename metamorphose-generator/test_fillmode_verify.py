#!/usr/bin/env python3
"""fill mode修正の検証テスト v5 Phase B。
per-stageマスク（段階的マスク拡張）+ 下着フェーズ追加で衣装変身を防止。
  v3: fill=0(blur)がstage3-4で体型・顔を破壊
  v4: fill=original全ステージ + CFG 9.0 → 衣装変身（TRON/メイド服化）
  v5A: ポーズKW追加 → 体回転修正されたが衣装変身は未解決
  v5B: per-stageマスク + 下着フェーズで段階的に脱衣
    Stage 0: 衣服乱れ → 上着マスクのみ
    Stage 1: 半脱ぎ(ブラ見え) → 上着マスクのみ
    Stage 2: 下着姿(ブラ+パンツ) → 上着+下衣+レッグウェア(外衣全部)
    Stage 3: トップレス(パンツのみ) → 全衣服マスク
    Stage 4: 全裸 → 全衣服マスク + fill=0
  v5R: reference_only全ステージ → Image1改善、Image4/15で青TRON・ペンギン化
  v5U: reference_only Stage0-2のみ + 重み0.3 + guidance_end0.4
    大マスクStage3-4ではreference_only無効化してOpenPoseのみに戻す
  v5V: 複合改善
    1. マスク外後処理: SD出力後にマスク外をprev_imgで上書き（Image2,15のマスク外劣化対策）
    2. legwear除外: Stage3-4のmask_allからlegwearを除外（Image1膝下消失対策）
    3. reference_only縮小: Stage0-1のみON (Image4青アーティファクト対策)
    4. denoising_end 0.85→0.78 (体型変化・マスク外劣化を抑制)
  v5W: 追加改善
    1. Stage2のmask_outerからもlegwear除外 → チェーン伝搬による膝下消失を根本解決
    2. Stage2プロンプトから(bare legs:1.2)削除 (legwear領域がマスク外になるので不要)
    3. denoising_end 0.78→0.73 (体形細化・青アーティファクト抑制)
    4. progressポーリングフォールバック時のJPEG検出ログ追加
  v5X: アーティファクト対策
    1. マスクdilate(7x7 ellipse, iter=2): 境界衣服ピクセルをマスク内に取り込み衣装ノイズ防止
    2. mask_blur 8→4: 境界シャープ化で衣服コンテキスト混入を抑制
    3. NEG_CLOTHINGにTRON系タームを追加 (tron, glowing lines, neon pattern, circuit pattern)
    4. live_previews_enable=False: TAESD無効化でCFフォールバック画像のVAE品質を改善
  v5Y: 境界・legwear・脱衣強度改善
    1. Stage3-4のmask_allにlegwear追加: Stage3-4でSD自然描画→境界ねじれ(Image15)解消
       Stage2のみlegwear除外を維持（チェーン伝搬による膝下消失防止）
    2. blend_mask_blur=16: post-processing blend用Gaussianを拡大→タイツ/太もも境界段差(Image1)改善
       mask_blur=4はSD APIへのマスクぼかし（境界シャープ）として独立維持
    3. DENOISING_END 0.73→0.77: Stage4のden=0.77→完全脱衣(Image2)改善
    4. reference_only完全無効化: TRON系ノイズのチェーン伝搬(Image4)を根本除去
  v5AB: 顎〜上着ギャップ埋め + 境界拡大 + dilate拡大
    1. fill_collar_gap: 顔検出の chin_y から上着マスク上端まで塗りつぶし → 襟を確実にマスクに取り込む
       filtered_out["_face_rects"] を app.py から取得（haarcascade 検出結果を保存）
    2. BOUNDARY_NEUTRALIZE_PX 28→50: より広い帯域でグレー化
    3. dilate kernel 7x7→9x9 (iter=3): ボタンライン等の細部をより広く取り込む
  v5AC: fill_collar_gap条件バグ修正 + neutralize内側化
    1. fill_collar_gap条件修正: chin_y > jacket_top_y → jacket_top_y > chin_y (条件が逆だった)
       正しい意味: 上着ボディが顎より下にある（隙間あり）→ chin_y:jacket_top_y を塗りつぶし
       旧コードでは Image1/2/4 に適用されず、Image15 に誤って大きすぎるfillが発生
    2. neutralize_mask_boundary をマスク内側のみに変更:
       旧: マスク外側の境界帯をグレー化 → Image15 の背景が灰色に
       新: マスク内側の境界帯のみグレー化 → 外側は元の色を保持
  v5AD: 外側中和再導入 + 顔保護 + 顔なし時collar fallback + x幅拡大
    1. neutralize外側帯域復活: 内側50px + 外側15px（背景影響なし）→ Image4青・Image15タイツ再修正
    2. fill_collar_gap x幅拡大: 顔幅±25% → 顔幅±100% (セーラー襟等の幅広カラーに対応)
    3. fill_collar_gap_nofacef: 顔検出なし時のfallback → 上着マスク上端を80px上方向延長
    4. build_stage_masks: 顔領域保護 → dilation後に face_rects 範囲をゼロ化して髪の変化を防止
  v5AE: 顔保護スコープ縮小 + 襟捕捉強化
    1. 顔保護y範囲をfh*55%に縮小: 顎・首回りをマスクに残して襟取り込みを維持（Image1）
    2. 顔保護をStage0-1のみに制限: Stage2-4の下半身マスク面積を保全（Image15回帰修正）
    3. fill_collar_gap: jacket_top_yから無条件40px上方延長（jacket≤chin時の襟バンド捕捉）
    4. fill_collar_gap_nofacef: 幅を中央1/3→2/3に拡大（幅広カラー対応、Image2/4）
  v5AK: 帽子/頭保護 + 段階的脱衣 + 色ブリード修正 + 襟残存修正
    1. 帽子マスク除外: dilation後に帽子領域(dilated 11x11 iter=2)を全ステージマスクからbitwise_and除外
    2. 顔保護y上方拡張: y1=fy → fy-fh*0.5（前髪・額保護）
    3. マスク構造変更: [u,u,outer,all,all] → [u,u,u,outer,all]（3段階上着脱衣）
    4. プロンプト段階化: Stage2=上着脱ぎ完了(ブラのみ)、Stage3=トップレス+パンティー
    5. ネガティブ調整: Stage2=外衣フル否定、Stage3=外衣+スカート否定(下着許可)
    6. blend_mask_blur 16→12: 色ブリード範囲縮小
    7. dilation iter 3→4: 制服残存ピクセルの確実な包含
    8. collar_extend 80→100 + x範囲拡大(1.5fw〜2.5fw): 幅広襟・肩回り捕捉
    9. collar延長ガード: 顔上端で延長を打ち切り + nofacefのjacket_top<10%スキップ
  v5AJ: upper-expand から顔保護を削除（二重適用バグ修正）
    upper-expand 内で face_rects をゼロ化すると dilate 後のマスクが縮小（7.4%→4.9%）。
    build_stage_masks() が既に顔保護を適用するため upper-expand での顔保護は不要。
  v5AI: upper-expand を closing→dilate に変更（sparse mask での縮小を防止）
    closing 81px は sparse mask を縮小させた（7.4%→2.6%）。dilate 61px に変更。
  v5AH: Image15 上着過小検出を closing 補完で解決
    1. 上着マスク < 15% 時に 81px closing で拡張（BG-removal サーバーなし版補完）
       FASHN/SegFormer が全身ポーズ制服で上着を過小検出する問題を回避
       拡張後に face_rects 領域を除去して顔への侵入を防止
       根本原因: bg-removal-server(port 8470)が停止中で BG-removal 補完がスキップされていた
  v5AG: 脱ぎ速度調整 + Image15 脱衣回復 + 顔崩壊修正
    1. STAGE_FILL_MODES [0,0,1,1,1]→[0,1,1,1,1]: Stage1をoriginalに戻す（脱ぎ早すぎ修正）
       Stage0のみblurで衣服色除去の起点を中性化、Stage1以降はoriginalで体型維持
    2. 上着過小検出fallback: Stage0-2のupper_px < combined_px*40% → Stage0-2をcombined_maskで代替
       Image15上着=11%で実態40%超のセグメント失敗時に脱衣が止まる問題を解消
    3. 顔保護x幅 ±fw/2→±fw: 目・口領域も確実にカバーしてImage15顔崩壊を防止
  v5AF: 襟除去根本修正 + Image15 weird 修正
    1. mask_outer/mask_all に mask_upper を使うバグ修正: collar fill が Stage2-4 にも伝搬する
       従来は safe_or(upper, lower) でcollar fill未適用のまま → Stage2-4でcollarが再出現
    2. STAGE_FILL_MODES Stage0-1 を fill=0(blur): 元collar色をSDに見せない → 除去しやすい
       fill=1(original)はSD が元collar ピクセルから denoising するため 60% denoising では不完全
    3. collar_extend_px 40→80px: 幅広な襟バンドにも対応
    4. 顔保護を全ステージに復元（y2=fh*55%維持）: Stage2-4顔保護削除によるImage15 weird修正
  v5AA: 細部マスク拡張 + 色ブリード帯域拡大
    1. dilate iterations 2→3: ジャケット上端を ~3-4px 追加拡張 → 襟・ボタンライン捕捉
    2. BOUNDARY_NEUTRALIZE_PX 15→28: 大面積衣服（Image4青）の影響圏を超える
  v5Z: 汎用色ブリード対策
    1. neutralize_mask_boundary: マスク境界外側 boundary_px 幅をグレースケールへ漸近
       衣服色に依存しない汎用処理。SDへのinit_imageのみ適用、prev_imgは保持。
       distanceTransform は 255-mask で正しくマスク外側を計算。
    2. legwear を Stage3-4 から再除外: v5x状態に戻す（膝下消失防止）
    3. DENOISING_END 0.77→0.73: Identity loss・膝消失を防ぐ
"""
import os
import sys
import json
import time
import cv2
import numpy as np
from PIL import Image
from pathlib import Path

# ── SD URL を sd_url.json から読み込み ──
_sd_url_candidates = [
    Path(__file__).resolve().parent.parent / "sd_url.json",
    Path(__file__).resolve().parent / "sd_url.json",
]
SD_URL = ""
for p in _sd_url_candidates:
    try:
        SD_URL = json.loads(p.read_text(encoding="utf-8")).get("url", "").rstrip("/")
        if SD_URL:
            break
    except (OSError, json.JSONDecodeError):
        continue
if not SD_URL:
    print("ERROR: sd_url.json not found or empty")
    sys.exit(1)

SD_AUTH = ""

IMG_DIR = r"I:\マイドライブ\Antigravity\workflow-gravity\output\raw\000_Original_プロンプト保管用\005_鳳_咲良_Original\2026-03-20_0613_鳳_咲良_police_uniform,navy_blue_police_jacket,matching_skirt"
OUT_DIR = os.path.join(os.path.dirname(__file__), "test_fillmode_v5ak_output")

BASE_PROMPT = "masterpiece, best quality, 1girl, solo, brown hair, bun hair, golden eyes, large breasts"
NEG_PROMPT = "worst quality, low quality, blurry, extra fingers, bad anatomy, deformed, 2girls, multiple girls, multiple people, group, picture frame, inset, picture-in-picture, split screen, collage, border"
NEG_CLOTHING = "(clothing:1.3), (uniform:1.3), (jacket:1.3), (skirt:1.3), (shirt:1.2), (police uniform:1.3), (navy blue jacket:1.3), (collar:1.2), (tie:1.2), (button:1.2), (teal clothing:1.3), (green clothing:1.3), (colored fabric:1.3), (fabric:1.2), (textile:1.2), (tron:1.3), (glowing lines:1.2), (neon pattern:1.2), (circuit pattern:1.2)"

# ステージ再設計: 下着フェーズ入り5段階
STAGES_FRONT = [
    "facing viewer, slightly disheveled clothes, unbuttoned collar, loose tie, (cleavage peek:1.2), flustered, blushing, sweat",
    "facing viewer, (open top:1.2), (white bra visible:1.2), (cleavage:1.2), (midriff showing:1.2), (bare skin:1.1), half-undressed, embarrassed",
    "facing viewer, (police jacket removed:1.2), (white bra:1.3), (bare shoulders:1.2), (bare midriff:1.2), (bare skin:1.2), standing, embarrassed, blushing",
    "facing viewer, (topless:1.3), (bare breasts:1.3), (nipples:1.2), (white panties only:1.3), (bare skin:1.3), (no bra:1.2), shy expression, blush",
    "facing viewer, (completely nude:1.3), (naked:1.3), (bare skin:1.3), (nipples:1.2), (navel:1.2), (smooth skin:1.3), shy smile, bedroom eyes",
]
STAGES_BACK = [
    "from behind, back view, slightly disheveled clothes, loose collar, untucked shirt, flustered, blushing, sweat",
    "from behind, back view, (half-undressed:1.2), (bra strap visible:1.2), (bare shoulders:1.2), (bare skin:1.1), embarrassed",
    "from behind, back view, (jacket removed:1.2), (bra strap:1.3), (bare back:1.2), (bare shoulders:1.2), (bare skin:1.2), standing, embarrassed, blushing",
    "from behind, back view, (topless:1.3), (bare back:1.3), (panties only:1.3), (exposed skin:1.3), (no bra:1.2), shy expression, blush",
    "from behind, back view, (completely nude:1.3), (naked:1.3), (bare skin:1.3), (smooth skin:1.3), (bare back:1.3), (bare butt:1.2), shy smile",
]

POSE_THRESHOLD = 8.0
# v5U: reference_only Stage0-2のみ有効、Stage3-4は無効化
#      重み0.45→0.3、guidance_end0.6→0.4で過剰テクスチャ転写を抑制
#      大マスクStage3-4でのTRON化・ペンギン化アーティファクトを防止
STAGE_FILL_MODES = [0, 1, 1, 1, 1]  # Stage 0のみblur(衣服色を消す起点)、Stage 1以降はoriginal(体型維持)
# inpaint_full_res: 全ステージFalse(Whole picture)
INPAINT_FULL_RES_PER_STAGE = [False, False, False, False, False]
DENOISING_START = 0.60
DENOISING_END = 0.73
STEPS = 28
CFG_SCALE = 7.5
SEED = 42
SAMPLER = "Euler a"
SCHEDULER = "Karras"
ADETAILER = False
MASK_BLUR = 4          # SD APIへのマスクぼかし（境界シャープ維持）
BLEND_MASK_BLUR = 12   # post-processing blend用Gaussian（境界段差の平滑化、16→12で色ブリード軽減）
BOUNDARY_NEUTRALIZE_PX = 50  # マスク外境界帯域グレー化幅（色ブリード防止）
INPAINT_FULL_RES_PADDING = 64
CONTROLNET_OPENPOSE = True
CONTROLNET_REFERENCE = False       # v5Y: reference_only完全無効化（TRON系ノイズ根本除去）
REFERENCE_STAGES = [False, False, False, False, False]
REFERENCE_WEIGHT = 0.3
REFERENCE_GUIDANCE_END = 0.4
CHAIN_BREAK_STAGE = None

# テスト画像: FRONT 2枚 + BACK 1枚 + Image 2(標準構図)
TEST_IMAGES = [1, 4, 15, 2]


def build_neg_prompts(base_neg, clothing_neg, n_stages):
    """Stage 0は衣服ネガティブなし、Stage 1以降は衣服ネガティブ追加。
    ただしStage 2(下着フェーズ)は下着をネガティブから除外。"""
    negs = []
    for i in range(n_stages):
        if i == 0:
            negs.append(base_neg)
        elif i == 2:
            # Stage 2: 上着マスクのみ → 外衣フル否定（下着は許可）
            outer_neg = "(jacket:1.3), (skirt:1.3), (shirt:1.2), (police uniform:1.3), (navy blue jacket:1.3), (collar:1.2), (tie:1.2), (button:1.2), (teal clothing:1.3), (green clothing:1.3), (colored fabric:1.3), (fabric:1.2), (textile:1.2)"
            negs.append(f"{base_neg}, {outer_neg}")
        elif i == 3:
            # Stage 3: outer(上+下)マスク → 外衣+スカート否定、下着は許可
            outer_neg = "(jacket:1.3), (skirt:1.3), (shirt:1.2), (police uniform:1.3), (navy blue jacket:1.3), (collar:1.2), (tie:1.2), (button:1.2), (teal clothing:1.3), (green clothing:1.3), (colored fabric:1.3)"
            negs.append(f"{base_neg}, {outer_neg}")
        else:
            negs.append(f"{base_neg}, {clothing_neg}")
    return negs


def fill_collar_gap(mask, face_rects, img_shape):
    """顔(顎)と上着マスクの間のギャップを埋めて襟・首回りをマスクに追加。

    顔検出の bounding box から顎位置を推定し、上着マスク上端との間を塗りつぶす。
    x範囲は顔幅±100%（セーラー襟など幅広カラーに対応）。
    """
    if mask is None or not face_rects:
        return mask
    h, w = img_shape[:2]
    filled = mask.copy()
    for (fx, fy, fw, fh) in face_rects:
        chin_y = fy + fh  # 顎の下端（face rectの下辺）
        # 水平範囲: 顔幅の左右150%マージン（セーラー襟・肩回りの幅広カラーに対応）
        x_start = max(0, fx - int(fw * 1.5))
        x_end = min(w, fx + int(fw * 2.5))
        # 上着マスクの上端（x範囲内の非ゼロ最小y）
        col_region = mask[:, x_start:x_end]
        nonzero_rows = np.where(np.any(col_region > 128, axis=1))[0]
        if len(nonzero_rows) == 0:
            continue
        jacket_top_y = nonzero_rows.min()
        # 顎から上着上端までのギャップを塗りつぶす
        # jacket_top_y > chin_y: 上着ボディが顎より下 → chin_y〜jacket_top_y が襟エリア
        if jacket_top_y > chin_y:
            gap_top = max(0, chin_y)
            filled[gap_top:jacket_top_y, x_start:x_end] = 255
            print(f"    [collar-gap] face=({fx},{fy},{fw},{fh}) chin_y={chin_y} jacket_top={jacket_top_y} fill={jacket_top_y-gap_top}px")
        # 無条件上方延長: jacket_top_yから40px上へ（襟バンド捕捉）
        # jacket_top_y <= chin_y の場合もここに到達する（上着が既に顎付近にある場合）
        collar_extend_px = 100
        extend_to_y = max(0, jacket_top_y - collar_extend_px)
        # 顔領域に侵入しないよう制限
        fy_top = fy  # 顔上端y
        extend_to_y = max(extend_to_y, fy_top)
        if extend_to_y < jacket_top_y:
            filled[extend_to_y:jacket_top_y, x_start:x_end] = 255
            print(f"    [collar-extend] jacket_top={jacket_top_y} extend={jacket_top_y - extend_to_y}px (capped at face_top={fy_top})")
    return filled


def fill_collar_gap_nofacef(mask, img_shape, extend_px=80):
    """顔検出なし時のfallback: 上着マスク上端を extend_px 上方向に延長して襟エリアを取り込む。"""
    if mask is None or img_shape is None:
        return mask
    h, w = img_shape[:2]
    filled = mask.copy()
    # 中央2/3の範囲でマスク上端を検出（幅広カラー対応）
    cx1, cx2 = w // 6, 5 * w // 6
    col_region = mask[:, cx1:cx2]
    nonzero_rows = np.where(np.any(col_region > 128, axis=1))[0]
    if len(nonzero_rows) == 0:
        return mask
    jacket_top_y = nonzero_rows.min()
    # jacket_topが画像上端付近(10%以内)のとき延長をスキップ（髪がjacketと誤認の可能性）
    if jacket_top_y < h * 0.10:
        print(f"    [collar-gap-nofacef] jacket_top={jacket_top_y} < 10% of h={h}, skip extension")
        return filled
    extend_to_y = max(0, jacket_top_y - extend_px)
    filled[extend_to_y:jacket_top_y, cx1:cx2] = 255
    print(f"    [collar-gap-nofacef] jacket_top={jacket_top_y} extend={jacket_top_y - extend_to_y}px")
    return filled


def build_stage_masks(cat_masks, face_rects=None):
    """カテゴリマスクからステージごとの段階的マスクを構築。

    Stage 0-2: 上着マスクのみ (3段階で段階的に上着を脱衣)
               face_rects が利用可能な場合、顎〜上着上端のギャップを埋めて襟を取り込む
               帽子マスクはdilation後に除外して帽子の変化を防止
    Stage 3:   上着+下衣 (legwear除外 → チェーン伝搬による膝下消失防止)
    Stage 4:   上着+下衣+下着 (legwear除外 → 膝下消失防止)
    """
    upper = cat_masks.get("上着")
    lower = cat_masks.get("下衣")
    underwear = cat_masks.get("下着")
    # legwear は全ステージでマスク除外（膝下消失防止）
    # Stage3-4の境界ねじれは neutralize_mask_boundary で対処する

    def safe_or(*masks):
        """None を除外して bitwise_or"""
        valid = [m for m in masks if m is not None]
        if not valid:
            return None
        result = valid[0].copy()
        for m in valid[1:]:
            result = cv2.bitwise_or(result, m)
        return result

    # img_shape をマスクから取得（collar gap fill に必要）
    _any_mask = next((m for m in [upper, lower, underwear] if m is not None), None)
    img_shape = _any_mask.shape if _any_mask is not None else None

    # Stage 0-1: 上着のみ + 顎〜上着上端のギャップ埋め（襟取り込み）
    if face_rects and img_shape is not None:
        mask_upper = fill_collar_gap(upper, face_rects, img_shape)
    elif img_shape is not None:
        mask_upper = fill_collar_gap_nofacef(upper, img_shape)  # 顔検出なし時fallback
    else:
        mask_upper = upper

    # Stage 2: 外衣(上着+下衣)、legwear除外（チェーン伝搬による膝下消失を防止）
    # legwearをStage2でマスクするとSDが膝下をbare legsに書き換え→Stage3-4に伝搬する
    # mask_upper（collar fill済み）を使ってStage2-4にも襟マスクを伝搬する
    mask_outer = safe_or(mask_upper, lower)

    # Stage 3-4: 全衣服(下着含む)、legwear除外（膝下消失防止）
    # legwear境界のねじれは neutralize_mask_boundary による色ブリード抑制で対処
    mask_all = safe_or(mask_upper, lower, underwear)

    # マスクdilate: 境界を7px膨張させて衣服ピクセルをマスク内に取り込む
    # → 境界付近の衣服テクスチャがSDのコンテキストに入らなくなりアーティファクト防止
    _dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

    def dilate_mask(m):
        if m is None:
            return None
        return cv2.dilate(m, _dilate_kernel, iterations=4)

    masks = [
        dilate_mask(mask_upper), dilate_mask(mask_upper),
        dilate_mask(mask_upper),                                 # Stage 2: まだ上着のみ（段階的脱衣）
        dilate_mask(mask_outer), dilate_mask(mask_all),          # Stage 3: 上+下, Stage 4: 全衣服
    ]

    # --- 帽子保護: dilation後に帽子領域をマスクから除外 ---
    hat = cat_masks.get("帽子")
    if hat is not None:
        hat_dilated = cv2.dilate(hat, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)), iterations=2)
        for i in range(len(masks)):
            if masks[i] is not None:
                masks[i] = cv2.bitwise_and(masks[i], cv2.bitwise_not(hat_dilated))
        print(f"  [hat-protect] 帽子マスク除外 (dilated 11x11 iter=2)")

    # dilation後に顔・髪領域をゼロ化（SDによる顔/髪変更を防止）
    if face_rects and img_shape is not None:
        h, w = img_shape[:2]
        for (fx, fy, fw, fh) in face_rects:
            # 顔上端より上50%拡張（前髪・額保護）〜鼻上（fh*55%）まで保護
            y1 = max(0, fy - int(fh * 0.5))
            y2 = min(h, fy + int(fh * 0.55))
            x1 = max(0, fx - fw)          # ±fw幅（目・口領域も確実にカバー）
            x2 = min(w, fx + fw * 2)
            for i in range(len(masks)):
                # 全ステージで顔保護（y2=fh*55%で顎・首回りはマスクに残す）
                if masks[i] is not None:
                    masks[i][y1:y2, x1:x2] = 0

    # 各マスクのカバレッジをログ出力
    for i, m in enumerate(masks):
        if m is not None:
            pct = 100.0 * np.count_nonzero(m > 128) / m.size
            print(f"    Stage {i} mask: {pct:.1f}% shape={m.shape} dtype={m.dtype}")
        else:
            print(f"    Stage {i} mask: None (no mask)")

    return masks


TRIGGER_FILE = os.path.join(os.path.dirname(__file__), "rerun_trigger.json")


def _run_batch(out_dir, test_images, all_files,
               generate_progressive_keyframes, segment_clothing_sam, combine_category_masks):
    """テストバッチを1回実行する（モデルロード不要・再利用可能）。"""
    os.makedirs(out_dir, exist_ok=True)

    for img_num in test_images:
        fname = next((f for f in all_files if int(f.split("-")[0]) == img_num), None)
        if fname is None:
            print(f"  Image {img_num} not found, skip")
            continue

        img_out_dir = os.path.join(out_dir, f"{img_num:05d}")
        os.makedirs(img_out_dir, exist_ok=True)

        # スキップチェック
        existing = [f for f in os.listdir(img_out_dir) if f.endswith(".png")]
        if len(existing) >= 6:
            print(f"\nImage {img_num} - already done ({len(existing)} files), skip")
            continue

        print(f"\nImage {img_num}: {fname}...", flush=True)
        t0 = time.time()

        img_rgb = np.array(Image.open(os.path.join(IMG_DIR, fname)).convert("RGB"))

        # マスク生成
        print("  Generating masks...", flush=True)
        try:
            cat_masks, filtered_out = segment_clothing_sam(img_rgb, SD_URL)
        except Exception as e:
            print(f"  ERROR mask generation: {e}")
            continue

        # ポーズ判定: exclude_pct + OpenPoseキーポイント（鼻検出）で補完
        exclude_pct = filtered_out.get("_exclude_pct", 0.0)
        openpose_front = filtered_out.get("_openpose_front", None)
        is_front = (exclude_pct >= POSE_THRESHOLD) or (openpose_front is True)
        selected_stages = STAGES_FRONT if is_front else STAGES_BACK
        pose_label = "FRONT" if is_front else "BACK"
        pose_reason = f"exclude={exclude_pct:.1f}% openpose_nose={openpose_front}"
        print(f"  Pose: {pose_label} ({pose_reason})", flush=True)

        neg_per_stage = build_neg_prompts(NEG_PROMPT, NEG_CLOTHING, len(selected_stages))

        # 上着マスク過小検出補完（BG-removal 補完が不十分な場合の追加拡張）
        # FASHN/SegFormer が tight uniform + 全身ポーズで上着を過小検出した場合に補完
        # → dilate で外側拡張（closing は sparse mask で縮小するため使わない）
        _upper = cat_masks.get("上着")
        if _upper is not None:
            _h, _w = _upper.shape[:2]
            _total_px = _h * _w
            _upper_px = np.count_nonzero(_upper > 128)
            _upper_pct = 100.0 * _upper_px / _total_px
            if _upper_pct < 15.0:
                # 上着が 15% 未満: dilate で外側拡張（sparse maskでも膨張する）
                # 顔保護は build_stage_masks() で適用されるため、ここでは適用しない
                # （二重適用すると dilate 後のマスクが縮小される逆効果が起きる: 7.4%→4.9% バグ）
                _k_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61))
                _expanded = cv2.dilate(_upper, _k_dilate)
                _expanded_pct = 100.0 * np.count_nonzero(_expanded > 128) / _total_px
                print(f"  [upper-expand] {_upper_pct:.1f}% → {_expanded_pct:.1f}% (dilate 61px)")
                cat_masks["上着"] = _expanded

        # per-stageマスク構築
        print("  Building per-stage masks...", flush=True)
        stage_masks = build_stage_masks(cat_masks, face_rects=filtered_out.get("_face_rects", []))

        # フォールバック: 上着マスクがNoneの場合、全マスクで代替
        assignments = {"帽子": "除外"}
        combined_mask = combine_category_masks(cat_masks, assignments)
        if combined_mask is None:
            print("  WARNING: No mask at all, skip")
            continue

        # Noneマスクを全体マスクでフォールバック
        for si in range(len(stage_masks)):
            if stage_masks[si] is None:
                stage_masks[si] = combined_mask
                print(f"    Stage {si} mask: fallback to combined ({100.0 * np.count_nonzero(combined_mask > 128) / combined_mask.size:.1f}%)")

        # 上着過小検出フォールバック（Image15対策）:
        # Stage 0-2 の上着マスクが combined の 40% 未満 → 上着セグメントが失敗している可能性大
        # → Stage 0-2 を combined_mask で代替して脱衣を確実化
        combined_px = np.count_nonzero(combined_mask > 128)
        upper_px_s0 = np.count_nonzero(stage_masks[0] > 128) if stage_masks[0] is not None else 0
        if combined_px > 0 and upper_px_s0 / combined_px < 0.40:
            print(f"  [upper-fallback] upper({upper_px_s0}px) < 40% of combined({combined_px}px) → Stage 0-2 use combined")
            for si in range(3):  # Stage 0, 1, 2
                stage_masks[si] = combined_mask

        total_pct = 100.0 * np.count_nonzero(combined_mask > 128) / (img_rgb.shape[0] * img_rgb.shape[1])
        print(f"  Combined mask: {total_pct:.1f}% coverage", flush=True)

        # キーフレーム生成
        ref_label = f"reference_only Stage0-1 w={REFERENCE_WEIGHT}" if CONTROLNET_REFERENCE else "OpenPoseのみ"
        print(f"  Generating keyframes (Whole picture + fill=original + {ref_label} + mask保護)...", flush=True)
        try:
            keyframes = generate_progressive_keyframes(
                base_u8=img_rgb,
                stages=selected_stages,
                base_prompt=BASE_PROMPT,
                neg_prompt=NEG_PROMPT,
                sd_url=SD_URL,
                sd_auth=SD_AUTH,
                denoising_start=DENOISING_START,
                denoising_end=DENOISING_END,
                steps=STEPS,
                cfg_scale=CFG_SCALE,
                seed=SEED,
                sampler_name=SAMPLER,
                scheduler=SCHEDULER,
                adetailer=ADETAILER,
                adetailer_stages=1,
                mask_u8=combined_mask,
                mask_per_stage=stage_masks,
                inpaint_full_res=False,
                inpaint_full_res_per_stage=INPAINT_FULL_RES_PER_STAGE,
                inpainting_fill_per_stage=STAGE_FILL_MODES,
                mask_blur=MASK_BLUR,
                blend_mask_blur=BLEND_MASK_BLUR,
                boundary_neutralize_px=BOUNDARY_NEUTRALIZE_PX,
                inpaint_full_res_padding=INPAINT_FULL_RES_PADDING,
                controlnet_openpose=CONTROLNET_OPENPOSE,
                chain_break_stage=CHAIN_BREAK_STAGE,
                neg_prompt_per_stage=neg_per_stage,
                controlnet_reference=CONTROLNET_REFERENCE,
                controlnet_reference_weight=REFERENCE_WEIGHT,
                controlnet_reference_guidance_end=REFERENCE_GUIDANCE_END,
                controlnet_reference_stages=REFERENCE_STAGES,
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            continue

        for ki, kf in enumerate(keyframes):
            out_path = os.path.join(img_out_dir, f"stage_{ki:02d}.png")
            Image.fromarray(kf).save(out_path, "PNG")

        elapsed = time.time() - t0
        print(f"  Done: {len(keyframes)} keyframes in {elapsed:.1f}s", flush=True)

    print(f"\n=== All done! Output: {out_dir} ===")
    print("Per-stage masks: Stage 0-2=upper, Stage 3=outer(no legwear), Stage 4=all(no legwear)")
    print(f"reference_only: {REFERENCE_STAGES} | denoising: {DENOISING_START}→{DENOISING_END} | mask_blur={MASK_BLUR} blend_blur={BLEND_MASK_BLUR} neutralize={BOUNDARY_NEUTRALIZE_PX}px")


def main():
    print(f"SD URL: {SD_URL}", flush=True)

    # ── 重い依存のモック（テストではUI/ローカル推論不要） ──
    import types as _types

    _gr = _types.ModuleType("gradio")
    _gr.Error = type("Error", (Exception,), {})
    _gr.Warning = lambda msg: None
    _gr.Info = lambda msg: None
    class _MockProgress:
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def tqdm(self, it, *a, **kw): return it
    _gr.Progress = _MockProgress
    _gr.update = lambda **kw: kw
    _gr_themes = _types.ModuleType("gradio.themes")
    _gr_themes.Default = lambda **kw: None
    _gr.themes = _gr_themes
    sys.modules["gradio"] = _gr
    sys.modules["gradio.themes"] = _gr_themes

    print("Loading app.py modules...", flush=True)
    sys.path.insert(0, os.path.dirname(__file__))
    from app import (
        generate_progressive_keyframes,
        segment_clothing_sam,
        combine_category_masks,
    )
    print("Modules loaded.", flush=True)

    all_files = sorted([f for f in os.listdir(IMG_DIR) if f.endswith(".png")])
    funcs = (generate_progressive_keyframes, segment_clothing_sam, combine_category_masks)

    # 初回テスト実行
    _run_batch(OUT_DIR, TEST_IMAGES, all_files, *funcs)

    # ── トリガーファイル監視ループ（GPUメモリ保持）──
    # Claude Code が rerun_trigger.json を置いたら次のバッチを自動実行する。
    # {"out_dir": "test_fillmode_v5af_output", "test_images": [1, 4, 15, 2]}
    print(f"\n[STANDBY] Watching {TRIGGER_FILE} ... (Ctrl+C で終了)", flush=True)
    try:
        while True:
            time.sleep(3)
            if not os.path.exists(TRIGGER_FILE):
                continue
            try:
                cfg = json.loads(Path(TRIGGER_FILE).read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[TRIGGER] parse error: {e}", flush=True)
                continue
            os.remove(TRIGGER_FILE)
            next_out = cfg.get("out_dir", OUT_DIR)
            next_imgs = cfg.get("test_images", TEST_IMAGES)
            print(f"\n[TRIGGER] out_dir={next_out} images={next_imgs}", flush=True)
            _run_batch(next_out, next_imgs, all_files, *funcs)
            print(f"\n[STANDBY] Watching {TRIGGER_FILE} ...", flush=True)
    except KeyboardInterrupt:
        print("\n[EXIT] Stopped.", flush=True)


if __name__ == "__main__":
    main()
