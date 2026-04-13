"""
vision_limb_checker.py
======================
Vision LLM を使って、画像内の女性キャラクターの四肢（腕・脚）の
解剖学的異常を検出するモジュール。

接続先の優先順位:
  1. Antigravity/vision_url.json  → ローカル vLLM (OpenAI互換 API)
  2. 環境変数 XAI_API_KEY         → Grok Vision API
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from openai import OpenAI

logger = logging.getLogger(__name__)

MAX_IMAGE_DIM = 1024  # Vision LLM に送る最大辺長 (px)

LIMB_CHECK_PROMPT = """\
Inspect this AI-generated anime image for SEVERE defects. Output JSON only.

LIMB DEFECTS (only flag if attachment point is visible and far from image edge):
- MISSING LEG: leg absent when both hips clearly visible. Never flag missing arms.
- EXTRA: more than 2 arms OR more than 2 legs on ONE character — \
even if the extra limbs look natural (e.g., two pairs of arms: one pair raised above head \
AND another pair gripping hips/thighs = 4 arms total = EXTRA)
- WRONG ATTACHMENT: limb grows from wrong body part
- IMPOSSIBLE BEND: joint bends physically impossibly (knee sideways, foot 180° backward, \
face+buttocks facing same direction simultaneously, \
or calf/shin passing THROUGH buttock/hip area — hyperextended knee where lower leg \
folds behind the thigh toward the body with foot ending up behind or above the hip joint)
- BACKWARD LEG: a leg extends BEHIND the spine — anatomically impossible without joint reversal. \
Do NOT flag: legs sideways, knees bent with partner behind (doggy/spooning), legs wrapped around partner. \
ONLY flag when lower leg/foot goes past the spine plane AND no partner is behind to explain it.
- BODY FUSION: two characters' skin or body parts literally merge with NO visible boundary — \
e.g. one character's arm/hand/leg/thigh/calf physically passes INTO the other's torso, breast, thigh, or body \
with no separation line (NOT just touching/grabbing/overlapping where a visible edge exists). \
Leg-into-thigh or calf-into-thigh with zero boundary line also counts.

ADDITIONAL DEFECTS:
- MULTI-PANEL: image split into 2+ panels/sub-images with visible border (inset box, side-by-side). NOT speech bubbles or effects.
- DUPLICATE FEMALE: 2+ females both showing visible breasts AND feminine face simultaneously. Do NOT flag based on position alone — in sex scenes the penetrating partner on top is likely male even with long hair. Only flag when BOTH characters unambiguously have breasts+feminine face.
- BALD MALE: male's entire scalp is bare skin, zero hair. Never flag short/buzzcut/white/silver/gray hair.
- GENDER CONFUSION: character in penetrating/top role has clearly female body (visible breasts, feminine face, feminine figure) — the male was accidentally generated as female.
- REVERSED MALE: in face-to-face sex (missionary/seeding press/paizuri), male faces WRONG direction. \
Signs: (1) his buttocks near her face, face near her feet; \
(2) seeding press: his bare FEET visible at image TOP, her face at BOTTOM; \
(3) paizuri: his face near her lower body/groin while penis is at her chest; \
(4) missionary partial-reversal: his head is at her chest/breast level (NOT facing her face), \
AND his pelvis is visibly misaligned with hers making the penetration geometrically impossible \
from that angle (body not aligned end-to-end). \
Do NOT flag doggy-style. Do NOT flag cowgirl (male below, female on top — his face/chest near her torso is normal). \
Do NOT flag if their faces are close together (that is correct orientation).
- WRONG GENITALS: female has penis on her own body (futanari). \
ONLY flag when you have UNAMBIGUOUS evidence of futanari anatomy — NOT normal penetration. \
Do NOT flag: (1) shaft entering or at penetration point regardless of male body visibility, \
(2) paizuri (shaft between breasts), (3) any shaft at normal sex act position. \
(A) COWGIRL: shaft at groin junction = HIS. Only flag if shaft separate from entry AND \
pointing freely outward AND no male body below whatsoever. \
(B) STANDING/KNEELING male behind: shaft at front of her groin with no male in front = flag. \
(C) SOLO (no male person AND no sex act occurring): erect shaft visible = flag. \
(D) CLEAR FUTANARI: shaft visibly attached to female body in impossible position (e.g., \
upper abdomen, between breasts when no sex act) with no male nearby = flag. \
When unsure or shaft could be male's: ok=true.
- BACKGROUND MALE: extra male figure/body visible in background beyond the main 2 characters
- DEFORMED BODY PART: body part has a topologically impossible shape — literally twisted like a corkscrew/rope OR physically folded inside-out (e.g. breast twisted into a spiral rope, limb bent at a non-joint creating a crease or fold in the skin). NOT just large, smooth, round, bulbous, or exaggerated size — those are normal in anime art. ONLY flag if the geometry itself is broken/inverted.

ALWAYS OK:
- Limbs out of frame, hidden by bodies/clothing/hair
- Arms invisible in any sexual scene (hidden between bodies)
- Unusual anime poses, foreshortening
- One male with any hairstyle + one female = fine
- Normal overlapping with visible edge between bodies
- Large, smooth, round, or very exaggerated buttocks (any size) — NOT DEFORMED unless physically inverted or folded
- Missionary where male's face is near female's face (correct orientation — do NOT flag Sign(4))
- Cowgirl (male on bottom, female on top): male's face/chest naturally at female torso level — NOT REVERSED MALE
- A penis shaft whose ROOT/BASE connects to a visible distinct male pelvis, \
  male hips, or male lower body — this is ALWAYS the male's penis. Do NOT \
  flag as WRONG GENITALS regardless of how much of the shaft is visible. \
  Even in cowgirl: if the shaft clearly enters the female from below and its \
  base connects to a male body below her, it is the male's shaft entering her.
- In penetration from behind (doggy/standing): male's shaft entering from \
  behind and partially visible = normal. Only flag if it appears at the FRONT \
  of the female's groin with no male body in front to explain it.

When in doubt: ok=true. No character visible: ok=true.
JSON only (no other text):
{"ok": true, "issues": [], "confidence": 0.9}
{"ok": false, "issues": ["WRONG GENITALS: erect shaft visible at female groin in cowgirl position"], "confidence": 0.85}
"""


LIMB_CHECK_PROMPT_PASS2 = """\
You are checking an AI-generated anime image for STRUCTURAL defects only.
Check ONLY these 4 specific categories — do NOT look for anything else:

1. MISSING BODY: A character's ENTIRE upper body (torso+head) OR entire lower \
body (legs) is completely absent — NOT cropped by the image frame edge, and NOT \
hidden behind the other character. Only flag if the body section is genuinely \
missing and the character appears to have only half a body.
   Do NOT flag: body parts hidden behind another character, body parts simply \
out of frame, or any "missing arm" scenarios.

2. BODY FUSION: Two characters' body parts physically merge with NO visible \
boundary between their skin surfaces — e.g. one character's arm/hand/leg/thigh/calf visually \
disappears INTO the other character's torso, breast, or thigh with no separation line. \
Leg/thigh passing through another character's thigh or buttocks with no boundary line also counts. \
Different from normal overlapping (visible edge present) or touching/grabbing \
(hand/leg stays on surface, not merged through it).

3. CONTRADICTORY POSITION: The sexual act depicted is geometrically impossible \
given the body orientations — e.g. the receiving character is clearly face-up \
(supine) but penetration angle requires them to be face-down.
   Only flag clearly impossible orientations. Unusual angles or ambiguous cropping \
should NOT be flagged. When in doubt, return ok=true.

4. DUPLICATE FEMALE: 2+ female characters both clearly showing visible breasts \
AND unambiguous feminine face simultaneously in the same image. \
In sex scenes, the character in the penetrating/top role may be male even with \
long hair — do NOT flag based on hair or position alone. Only flag when BOTH \
characters unambiguously have breasts AND feminine face with NO possibility \
that the upper character is male. When in doubt, return ok=true.

ACCEPTABLE (return ok=true for ALL of these):
- Unusual anime poses, foreshortening, extreme flexibility
- Body parts hidden behind another character or out of frame
- Any scene where defects are not clearly visible
- Penetrating/top character in sex scenes (may be male despite long hair)
- When in doubt, return ok=true

JSON only (no other text):
{"ok": true, "issues": [], "confidence": 0.9}
{"ok": false, "issues": ["BODY FUSION: two characters share the same skin surface"], "confidence": 0.9}
"""


# ──────────────────────────────────────────────────────────────
# Issue フィルターパターン（FP 除去用正規表現リスト）
# ──────────────────────────────────────────────────────────────

# Pass 1 フィルター（全カテゴリ対象）
_FILTER_PATS_PASS1 = [
    re.compile(r"\barm\b.{0,40}\b(absent|missing)\b", re.IGNORECASE),  # arm absent — NSFW で腕が体の間に隠れる
    # re.compile(r"\breversed\s+male\b", re.IGNORECASE),                  # REVERSED MALE — Phase 2 以降は無効化
    re.compile(r"\b(?:BALD|BOLD)\s+MALE\b", re.IGNORECASE),  # BALD/BOLD MALE — NGimage TP にこのカテゴリなし、全除去
    re.compile(r"\bBACKGROUND\s+MALE\b", re.IGNORECASE),  # BACKGROUND MALE — NGimage TP にこのカテゴリなし、全除去
    re.compile(r"BALD.{0,80}\b(white|silver|gray|grey|light|blonde)\b.{0,40}hair", re.IGNORECASE | re.DOTALL),  # BALD MALE — 白/銀髪を禿げと誤検知した場合のみ除去（旧フィルター、新フィルターが優先）
    re.compile(r"\b(character|person)\b.{0,40}\bnot\s+a\s+male\b", re.IGNORECASE),  # 防衛的記述
    re.compile(r"DUPLICATE\s+FEMALE.*\bGENDER\s+CONFUSION\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"DUPLICATE\s+FEMALE.*\binverted\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"GENDER\s+CONFUSION.*\bmuscular\s+build\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"OVERLAP CONTRADICTION.*(?:underneath|beneath) the female", re.IGNORECASE | re.DOTALL),
    re.compile(r"OVERLAP CONTRADICTION.*pass\s+underneath.*buttocks", re.IGNORECASE | re.DOTALL),
    re.compile(r"OVERLAP CONTRADICTION.*(?:legs?|thighs?|lower body).*(?:underneath|beneath)", re.IGNORECASE | re.DOTALL),
    re.compile(r"DUPLICATE\s+FEMALE.*\bpaizuri\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"REVERSED\s+MALE.*\bpositioned beneath\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY FUSION.*\bhips\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"DUPLICATE\s+FEMALE.*\bbeneath the main female\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"REVERSED\s+MALE.*\b(?:suit|tie)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"REVERSED\s+MALE.*\bcharacter on the bottom\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"DUPLICATE\s+FEMALE.*\b(?:performing oral|oral sex)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"DUPLICATE\s+FEMALE.*\bbent.over\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"DUPLICATE\s+FEMALE.*\bvisible beneath\b", re.IGNORECASE | re.DOTALL),
    # DUPLICATE FEMALE — "one on top, one on bottom" = cowgirl 等の正常体位（男性が下）
    re.compile(r"DUPLICATE\s+FEMALE.{0,200}\bone\s+on\s+(?:top|bottom)", re.IGNORECASE | re.DOTALL),
    # DUPLICATE FEMALE — 上側キャラが男性の可能性（penetrating/on top/male body）
    re.compile(r"DUPLICATE\s+FEMALE.{0,200}\b(?:penetrating\s+(?:character|partner|figure)|character\s+on\s+top|on\s+top\s+(?:is|appears?))\b", re.IGNORECASE | re.DOTALL),
    # WRONG GENITALS — shaft described as entering/penetrating (= male's shaft)
    re.compile(r"WRONG\s+GENITALS.{0,400}\bshaft.{0,150}\bentering\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"WRONG\s+GENITALS.{0,400}\bfrom\s+(?:below|underneath|the\s+male)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"WRONG\s+GENITALS.{0,400}\b(?:male|his).{0,80}\benter(?:s|ing)?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"WRONG\s+GENITALS.{0,400}\b(?:is|are|appears?|seems?|clearly)\s+insert(?:ed|ing)\b", re.IGNORECASE | re.DOTALL),
    # WRONG GENITALS — model itself says "could be the male's" or "unclear"
    re.compile(r"WRONG\s+GENITALS.{0,300}\b(?:could\s+be|might\s+be|possibly|unclear|ambiguous)\b", re.IGNORECASE | re.DOTALL),
    # WRONG GENITALS — cowgirl (NGimage TP すべてに DEFORMED/REVERSED/DUPLICATE バックアップあり)
    re.compile(r"WRONG\s+GENITALS.{0,200}\bcowgirl\b", re.IGNORECASE | re.DOTALL),
    # DEFORMED BODY PART — breast (NGimage TP の DEFORMED はすべて buttocks、breast は全 FP)
    re.compile(r"DEFORMED.{0,100}\bbreast", re.IGNORECASE | re.DOTALL),
    # REVERSED MALE — paizuri (NGimage TP の REVERSED MALE に paizuri 言及なし)
    re.compile(r"REVERSED\s+MALE.{0,300}\bpaizuri\b", re.IGNORECASE | re.DOTALL),
    # REVERSED MALE — モデルがルール文をそのまま出力した場合
    re.compile(r"REVERSED\s+MALE.{0,400}\bDo\s+NOT\s+flag\s+doggy.style\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"REVERSED\s+MALE.{0,200}\bSigns:\s*\(1\)\b", re.IGNORECASE | re.DOTALL),
    # REVERSED MALE — seeding press を paizuri と誤認したケース
    re.compile(r"REVERSED\s+MALE.{0,300}\bpenis\s+is\s+at\s+her\s+chest\b", re.IGNORECASE | re.DOTALL),
    # GENDER CONFUSION — NGimage TP は常に他の issue（Bald Male 等）を伴うため単独除去は安全
    re.compile(r"GENDER\s+CONFUSION", re.IGNORECASE),
    # REVERSED MALE — cowgirl は face-to-face に該当しない（Sign(4) 過検知対策）
    # NGimage TP の REVERSED MALE + cowgirl はなし。00293等は REVERSED MALE 独立あり
    re.compile(r"REVERSED\s+MALE.{0,200}\bcowgirl\b", re.IGNORECASE | re.DOTALL),
    # REVERSED MALE Sign(2) — seeding press で女性の上げた脚を男性の足と誤認するFP
    # VLM が "feet at image top" "feet visible at top of image" "top of image...feet" 等様々な語順で出力する
    re.compile(r"REVERSED\s+MALE.{0,400}\b(?:bare\s+)?feet.{0,200}\b(?:image\s+top|top\s+of\s+(?:the\s+)?image)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"REVERSED\s+MALE.{0,400}\btop\s+of\s+(?:the\s+)?image.{0,200}\b(?:bare\s+)?feet\b", re.IGNORECASE | re.DOTALL),
    # WRONG GENITALS — seeding press: "no male body visible below her" = 男性が上方にいるため（正常体位）
    re.compile(r"WRONG\s+GENITALS.{0,400}\bno\s+male.{0,100}\bbelow\b", re.IGNORECASE | re.DOTALL),
    # WRONG GENITALS — "shaft root not connected to any male pelvis" = seeding press 等で男性下半身が隠れているケース
    re.compile(r"WRONG\s+GENITALS.{0,400}\bshaft\s+root\s+not\s+connected\b", re.IGNORECASE | re.DOTALL),
    # WRONG GENITALS — "no male visible in front" = doggy-style/seeding press で男性が後ろ/上にいる（正常）
    re.compile(r"WRONG\s+GENITALS.{0,400}\bno\s+male.{0,100}\bin\s+front\b", re.IGNORECASE | re.DOTALL),
    # WRONG GENITALS — "between breasts" = paizuri で男性のシャフトが胸の間にある正常な体位
    re.compile(r"WRONG\s+GENITALS.{0,200}\bbetween\b.{0,30}\bbreasts?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"WRONG\s+GENITALS.{0,200}\bbreasts?\b.{0,50}\bbetween\b", re.IGNORECASE | re.DOTALL),
    # REVERSED MALE — "pelvis misaligned/impossible" = Sign(4)の曲解（missionary で頭と腰がずれているように見える正常体位）
    re.compile(r"REVERSED\s+MALE.{0,400}\bpelvis.{0,150}\b(?:misaligned|impossible|geometrically)\b", re.IGNORECASE | re.DOTALL),
    # IMPOSSIBLE BEND — "inverted/feet upward" = seeding press等で脚が上に伸びた正常体位（NGimage TP: 全回0件）
    re.compile(r"IMPOSSIBLE\s+BEND.{0,300}\b(?:inverted|feet\s+(?:facing|pointing)\s+upward|body\s+is\s+inverted)\b", re.IGNORECASE | re.DOTALL),
    # IMPOSSIBLE BEND — "knee bent sideways" = 性行為中の脚の角度誤認FP（NGimage TP にこのパターンなし）
    re.compile(r"IMPOSSIBLE\s+BEND.{0,200}\bknee\b.{0,100}\bsideways\b", re.IGNORECASE | re.DOTALL),
    # IMPOSSIBLE BEND — "calves/shins passing/folding through/into buttock/hip" = kneeling体位の誤認FP
    re.compile(r"IMPOSSIBLE\s+BEND.{0,300}\b(?:calves?|shins?)\b.{0,30}\b(?:pass(?:es|ing)?|appear\s+to\s+pass|fold(?:ing|s)?)\s+(?:through|into)\b", re.IGNORECASE | re.DOTALL),
    # IMPOSSIBLE BEND — "lower back hyperextended / spine curve backward" = cowgirl/doggy反り腰（正常体位 → FP）
    re.compile(r"IMPOSSIBLE\s+BEND.{0,300}\b(?:lower\s+back|lumbar)\b.{0,200}\b(?:hyperextend|curve\s+backward|arched\s+backward)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"IMPOSSIBLE\s+BEND.{0,300}\bspine\b.{0,100}\bcurve\s+(?:backward|inward)\b", re.IGNORECASE | re.DOTALL),
    # REVERSED MALE — fellatio シーン誤認（shaft at her mouth = 正常な oral sex, 逆向き penetration ではない）
    re.compile(r"REVERSED\s+MALE.{0,400}\bshaft.{0,100}\bmouth\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"REVERSED\s+MALE.{0,400}\bmouth.{0,100}\bshaft\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — penis/phallic object が breast/chest に merge（paizuri等。NGimage TP は REVERSED MALE バックアップあり）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:penis|phallic\s+(?:object|shape))\b.{0,150}\b(?:breast|chest)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:breast|chest)\b.{0,150}\b(?:penis|phallic\s+(?:object|shape))\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — arm/hand/fingers が breast/chest/torso に merge（触れている腕・手の区別不可）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:arms?|hands?|fingers?)\b.{0,150}\b(?:breast|chest|torso|body)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — "object" が merge（男性の体部位を"object"と曖昧に描写したFP）
    re.compile(r"BODY\s+FUSION.{0,250}\bobject\b.{0,200}\bmerge\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bobject\b.{0,150}\b(?:character|body|torso)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — 男性の頭部/上半身が女性の臀部に merge（doggy-style の近接を誤検知）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|upper\s+torso)\b.{0,150}\bbuttocks?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bbuttocks?\b.{0,150}\b(?:head|upper\s+torso)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — head/neck が thigh に merge（頭が太ももに入ることはない → 常にFP）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|neck)\b.{0,200}\bthighs?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bthighs?\b.{0,200}\b(?:head|neck)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — head/neck/torso が lower back/hip/waist に merge（cowgirl 等で頭部が腰近くに来る正常体位）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|neck|upper\s+torso)\b.{0,200}\b(?:lower\s+back|hip|waist)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:lower\s+back|hip|waist)\b.{0,200}\b(?:head|neck|upper\s+torso)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — arm/hand が thigh に merge（触れている/グリップしているだけ → 常にFP）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:arms?|hands?)\b.{0,150}\bthighs?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bthighs?\b.{0,150}\b(?:arms?|hands?)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — arm/hand が head/neck に merge（抱き寄せ/頭を押さえる正常動作 → 常にFP）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:arms?|hands?)\b.{0,150}\b(?:head|neck)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|neck)\b.{0,150}\b(?:arms?|hands?)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — shaft/hand が buttocks に merge（挿入シーンで臀部近くに shaft が来るのは正常）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:shaft|hand)\b.{0,200}\bbuttocks?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bbuttocks?\b.{0,200}\b(?:shaft|hand)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — penis/shaft が female body に merge（挿入シーン全般 → 正常。Pass1 にも追加）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:penis|shaft)\b.{0,200}\b(?:female|directly\s+into|into\s+the\s+female|without\s+visible\s+boundary|entry\s+point)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bshaft\b.{0,200}\bmerge", re.IGNORECASE | re.DOTALL),
    # DEFORMED BODY PART — buttocks "appear melted" = VLM が単に外観を誤解（本物の TP は "melted" のみ）
    re.compile(r"DEFORMED.{0,200}\bbuttock.{0,100}\bappear.{0,50}\bmelted\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"DEFORMED.{0,200}\bbuttock.{0,100}\bappear\s+to\s+be\b", re.IGNORECASE | re.DOTALL),
    # DEFORMED BODY PART — "enlarged/large/oversized" = プロンプトで除外指定されているサイズの問題（TP 00618 には enlargedなし）
    re.compile(r"DEFORMED.{0,200}\b(?:enlarged|oversized|severely\s+large)\b", re.IGNORECASE | re.DOTALL),
    # DEFORMED BODY PART — "severely distorted" バリアント（TP 00618 は "melted" のみで "severely distorted" なし → 安全）
    re.compile(r"DEFORMED.{0,200}\bbuttock.{0,100}\bseverely\s+distorted\b", re.IGNORECASE | re.DOTALL),
    # DUPLICATE FEMALE — "two characters" (gender 不確定) vs "two females" (確定) は FP の傾向
    re.compile(r"DUPLICATE\s+FEMALE.{0,200}\btwo\s+characters.{0,200}\bvisible\s+breasts\b", re.IGNORECASE | re.DOTALL),
    # DUPLICATE FEMALE — "two female[s/figures] both showing" (cowgirl等で男性を女性と誤認するFP)
    # TP の "2+ females" テキストは "two" を使わないため、このフィルターは TP を消去しない
    re.compile(r"DUPLICATE\s+FEMALE.{0,200}\btwo\s+female.{0,200}\bvisible\s+breasts\b", re.IGNORECASE | re.DOTALL),
    # DEFORMED BODY PART — "melted into blob with impossible geometry" バリアント
    # TP 00618 は "amorphous blob" のみ（"impossible geometry" なし）→ 安全にフィルター可
    re.compile(r"DEFORMED.{0,200}\bbuttock.{0,100}\bmelted.{0,200}\bimpossible\s+geometry\b", re.IGNORECASE | re.DOTALL),
    # DEFORMED BODY PART — "no gluteal cleft" / "continuous dome" （アニメ描法で正常 → FP）
    re.compile(r"DEFORMED.{0,300}\b(?:no\s+gluteal\s+cleft|gluteal\s+cleft|continuous\s+(?:smooth\s+)?dome|no\s+groove\s+between|lack\s+(?:a\s+)?(?:gluteal\s+)?cleft)\b", re.IGNORECASE | re.DOTALL),
    # DEFORMED BODY PART — "no cleavage or nipple separation" / "left/right merged" （アニメ的表現 → FP）
    re.compile(r"DEFORMED.{0,300}\b(?:no\s+cleavage|no\s+nipple\s+separation|no\s+(?:visible\s+)?cleavage|left.right\s+merged)\b", re.IGNORECASE | re.DOTALL),
    # REVERSED MALE — "face near female's feet, shaft entering from front" 倒立誤認FP
    # 実際は普通の体位だが男性の顔部分の向きを誤解したケース
    re.compile(r"REVERSED\s+MALE.{0,400}\bface\b.{0,200}\b(?:feet|foot)\b.{0,200}\bshaft.{0,100}entering\b", re.IGNORECASE | re.DOTALL),
    # BACKWARD LEG — モデルがルール文をそのまま出力した場合（"Do NOT flag" または "ONLY flag when" がissue内に含まれる）
    re.compile(r"BACKWARD\s+LEG.{0,100}\bDo\s+NOT\s+flag\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BACKWARD\s+LEG.{0,100}\bONLY\s+flag\s+when\b", re.IGNORECASE | re.DOTALL),
    # BACKWARD LEG — "lower legs" 複数形（膝まずき・doggy-style の正常な下腿後方折れを誤検知）
    re.compile(r"BACKWARD\s+LEG:[^/\n]*\blower\s+legs\b", re.IGNORECASE),
    # BACKWARD LEG — "lower leg/foot extends behind * torso plane"（"the" の有無を問わず）
    re.compile(r"BACKWARD\s+LEG:[^/\n]*\blower\s+leg/foot\s+extends\s+behind\b.{0,30}\btorso\s+plane\b", re.IGNORECASE),
    # BACKWARD LEG — "lower leg/foot extends behind * spine plane"（spine/back バリアント）
    re.compile(r"BACKWARD\s+LEG:[^/\n]*\blower\s+leg/foot\s+extends\s+behind\b.{0,40}\bspine\s+(?:plane|line)\b", re.IGNORECASE),
    # BACKWARD LEG — "lower leg/foot extends behind * back plane"（back バリアント）
    re.compile(r"BACKWARD\s+LEG:[^/\n]*\blower\s+leg/foot\s+extends\s+behind\b.{0,30}\bback\s+plane\b", re.IGNORECASE),
    # BACKWARD LEG — "extends behind spine/torso plane" が足の位置言及なし（thigh/knee が後ろに行くだけで足未確認 → FP）
    re.compile(r"BACKWARD\s+LEG:[^/\n]*\bextends\s+behind\b.{0,80}\b(?:spine|torso)\s+plane\b(?!.{0,300}\bfoot\b)", re.IGNORECASE | re.DOTALL),
]

# Pass 2 フィルター（Pass 2 の 4 カテゴリに関係するもののみ）
# REVERSED_MALE と IMPOSSIBLE_BEND は Pass 2 から除外（FP 多発 → DWPose と Pass 1 に委譲）
_FILTER_PATS_PASS2 = [
    re.compile(r"\barm\b.{0,40}\b(absent|missing)\b", re.IGNORECASE),
    re.compile(r"BALD.{0,80}\b(white|silver|gray|grey|light|blonde)\b.{0,40}hair", re.IGNORECASE | re.DOTALL),
    re.compile(r"DUPLICATE\s+FEMALE", re.IGNORECASE),  # Pass 2 では DF を全除外
    re.compile(r"REVERSED\s+MALE", re.IGNORECASE),     # Pass 2 では RM を全除外（FP 多発）
    re.compile(r"IMPOSSIBLE\s+BEND", re.IGNORECASE),   # Pass 2 では IB を全除外（DWPose に委譲）
    re.compile(r"GENDER\s+CONFUSION", re.IGNORECASE),  # Pass 2 では GC を全除外（FP 多発）
    # BODY FUSION — penis が breast/chest に merge（Pass2 でも同様に除去）
    re.compile(r"BODY\s+FUSION.{0,250}\bpenis\b.{0,150}\b(?:breast|chest)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:breast|chest)\b.{0,150}\bpenis\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — arm/hand/fingers が breast/chest/torso に merge（Pass2 でも同様に除去）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:arms?|hands?|fingers?)\b.{0,150}\b(?:breast|chest|torso|body)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — "object" が merge（Pass2 でも同様に除去）
    re.compile(r"BODY\s+FUSION.{0,250}\bobject\b.{0,200}\bmerge\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bobject\b.{0,150}\b(?:character|body|torso)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — penis が female body に merge（挿入シーン全般 → 正常な挿入を誤検知）
    re.compile(r"BODY\s+FUSION.{0,250}\bpenis.{0,150}\b(?:female|directly\s+into|into\s+the\s+female)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — head が vaginal/groin area に merge（69ポジション等 → 正常）
    re.compile(r"BODY\s+FUSION.{0,250}\bhead\b.{0,150}\b(?:vaginal?|groin|pubic|crotch)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:vaginal?|groin|pubic|crotch)\b.{0,150}\bhead\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — 男性の頭部/上半身が女性の臀部に merge（doggy-style → Pass2 にも同様適用）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|upper\s+torso)\b.{0,150}\bbuttocks?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bbuttocks?\b.{0,150}\b(?:head|upper\s+torso)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — head/neck が thigh に merge（Pass2 でも同様）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|neck)\b.{0,200}\bthighs?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bthighs?\b.{0,200}\b(?:head|neck)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — head/neck/torso が lower back/hip に merge（Pass2 でも同様）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|neck|upper\s+torso)\b.{0,200}\b(?:lower\s+back|hip|waist)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:lower\s+back|hip|waist)\b.{0,200}\b(?:head|neck|upper\s+torso)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — arm/hand が thigh に merge（Pass2 でも同様）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:arms?|hands?)\b.{0,150}\bthighs?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bthighs?\b.{0,150}\b(?:arms?|hands?)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — arm/hand が head/neck に merge（Pass2 でも同様）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:arms?|hands?)\b.{0,150}\b(?:head|neck)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:head|neck)\b.{0,150}\b(?:arms?|hands?)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — shaft/hand が buttocks に merge（Pass2 でも同様）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:shaft|hand)\b.{0,200}\bbuttocks?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bbuttocks?\b.{0,200}\b(?:shaft|hand)\b", re.IGNORECASE | re.DOTALL),
    # BODY FUSION — penis/shaft が female body に merge（Pass2 でも同様）
    re.compile(r"BODY\s+FUSION.{0,250}\b(?:penis|shaft)\b.{0,200}\b(?:female|directly\s+into|into\s+the\s+female|without\s+visible\s+boundary|entry\s+point)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"BODY\s+FUSION.{0,250}\bshaft\b.{0,200}\bmerge", re.IGNORECASE | re.DOTALL),
]

# BODY FUSION 保護パターン — フィルターで除去されても以下のキーワードがあればTP として復活させる
# 「肩関節がない」「誤った体部位から出現/通過/溶け込む」等の真の解剖学的融合を示す文言
# TP保護パターン: フィルターで除去されても以下のキーワードがあればTPとして復活させる
# BODY_FUSION / DEFORMED 両方に対応
_BODY_FUSION_PRESERVE_PATS = [
    # BODY FUSION TP: 真の解剖学的融合語（肩関節なし・誤った体部位から出現/通過/溶け込む）
    re.compile(
        r"BODY\s+FUSION.{0,800}"
        r"(?:"
        r"without\s+(?:a\s+)?(?:visible|proper|clear|any\s+visible)?\s*(?:shoulder|joint|articulation)\b|"
        r"no\s+(?:visible\s+|clear\s+|proper\s+)?(?:shoulder|joint|articulation)\b|"
        r"missing\s+(?:shoulder|joint)\b|"
        r"emerg(?:es?|ing)\s+(?:directly\s+)?(?:from|out\s+of)\b|"
        r"pass(?:es|ing)\s+through\b|"
        r"dissolv(?:ing|es)\s+into\b|"
        r"no\s+(?:visible\s+|clear\s+)?(?:joint|separation)\s+(?:between|at|point)\b|"
        r"without\s+(?:a\s+)?(?:clear\s+)?(?:joint|shoulder|separation)\b"
        r")",
        re.IGNORECASE | re.DOTALL
    ),
]


@dataclass
class LimbCheckResult:
    path: str
    ok: bool
    issues: list[str] = field(default_factory=list)
    confidence: float = 1.0
    error: str = ""


class VisionLimbChecker:
    """
    Vision LLM を使って四肢異常を判定するクラス。
    """

    def __init__(self, interval: float = 1.0) -> None:
        """
        Parameters
        ----------
        interval : float
            API 呼び出し間の待機秒数（レート制限対策）
        """
        self.interval = interval
        self._client, self._model = self._init_client()

    # ──────────────────────────────────────────────────────────────
    # 初期化
    # ──────────────────────────────────────────────────────────────

    def _init_client(self) -> tuple[OpenAI, str]:
        """接続先を優先順位に従って決定し、OpenAI クライアントを返す。"""
        # 1. vision_url.json (ローカル vLLM)
        vision_cfg = self._load_vision_url()
        if vision_cfg:
            base_url = vision_cfg.get("url", "").rstrip("/")
            # /v1 が含まれていない URL（例: http://localhost:8000）を補完する
            if not base_url.endswith("/v1"):
                base_url = base_url + "/v1"
                logger.info(f"[VisionLimb] /v1 を補完しました")
            model = self._resolve_model(base_url, vision_cfg.get("model"))
            logger.info(f"[VisionLimb] ローカル vLLM を使用: {base_url}  model={model}")
            client = OpenAI(api_key="dummy", base_url=base_url)
            return client, model

        # 2. Grok Vision API
        xai_key = os.getenv("XAI_API_KEY")
        if not xai_key:
            # .env ファイルを探して読み込む
            try:
                from dotenv import load_dotenv
                for candidate in [
                    Path(__file__).parent.parent / ".env",
                    Path(__file__).parent.parent.parent / ".env",
                ]:
                    if candidate.exists():
                        load_dotenv(str(candidate))
                        break
                xai_key = os.getenv("XAI_API_KEY")
            except ImportError:
                pass

        if xai_key:
            model = "grok-vision-beta"
            logger.info("[VisionLimb] Grok Vision API を使用 (model=%s)", model)
            client = OpenAI(api_key=xai_key, base_url="https://api.x.ai/v1")
            return client, model

        raise RuntimeError(
            "Vision LLM の接続先が見つかりません。\n"
            "  - Antigravity/vision_url.json を配置するか\n"
            "  - 環境変数 XAI_API_KEY を設定してください。"
        )

    @staticmethod
    def _load_vision_url() -> dict | None:
        """Antigravity/vision_url.json を読み込む。"""
        candidates = [
            # modules/ → workflow-gravity/ → Antigravity/
            Path(__file__).parent.parent.parent / "vision_url.json",
            # カレントディレクトリ直下
            Path.cwd() / "vision_url.json",
        ]
        for path in candidates:
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"vision_url.json 読み込み失敗: {e}")
        return None

    @staticmethod
    def _resolve_model(base_url: str, fallback: str | None) -> str:
        """vLLM の /models エンドポイントからモデル名を自動取得する。"""
        try:
            models_url = base_url.rstrip("/") + "/models"
            req = urllib.request.Request(models_url)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            return data["data"][0]["id"]
        except Exception as e:
            logger.warning(f"モデル名自動取得失敗: {e}  fallback={fallback}")
            if not fallback:
                raise RuntimeError(
                    "モデル名を自動取得できず、vision_url.json に model キーもありません。"
                )
            return fallback

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def check(self, image_path: str | Path) -> LimbCheckResult:
        """1 枚の画像をチェックして LimbCheckResult を返す。"""
        path = str(image_path)
        try:
            b64 = self._encode_image(path)
        except Exception as e:
            logger.error(f"画像エンコード失敗 ({path}): {e}")
            return LimbCheckResult(path=path, ok=True, error=f"encode error: {e}")

        try:
            raw = self._call_api(b64)
        except Exception as e:
            logger.error(f"API 呼び出し失敗 ({path}): {e}")
            return LimbCheckResult(path=path, ok=True, error=f"api error: {e}")

        return self._parse_response(raw, path)

    def check_batch(self, image_paths: list[str | Path]) -> list[LimbCheckResult]:
        """複数画像を逐次処理する（インターバルあり）。"""
        results = []
        for i, p in enumerate(image_paths):
            results.append(self.check(p))
            if i < len(image_paths) - 1:
                time.sleep(self.interval)
        return results

    def check_two_pass(self, image_path: str | Path) -> LimbCheckResult:
        """2-pass VLM チェック：Pass 1（全カテゴリ）+ Pass 2（4カテゴリ特化）の OR 結合。

        Pass 2 はプロンプトが短いため thinking トークン枠が広く、
        Pass 1 で見逃しやすいカテゴリを補完検出する。
        Pass 2 が例外を出した場合は Pass 1 の結果のみを返す。
        """
        result1 = self.check(image_path)
        path = str(image_path)
        try:
            b64 = self._encode_image(path)
            raw2 = self._call_api_pass2(b64)
            result2 = self._parse_response(raw2, path, filter_pats=_FILTER_PATS_PASS2)
        except Exception as e:
            logger.warning(f"Pass 2 失敗 ({Path(path).name}): {e}  — Pass 1 結果を使用")
            return result1

        if result1.ok and result2.ok:
            return LimbCheckResult(
                path=path,
                ok=True,
                issues=[],
                confidence=min(result1.confidence, result2.confidence),
            )

        merged = list(dict.fromkeys(result1.issues + result2.issues))
        return LimbCheckResult(
            path=path,
            ok=False,
            issues=merged,
            confidence=max(result1.confidence, result2.confidence),
        )

    # ──────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _encode_image(path: str) -> str:
        """画像を最大 MAX_IMAGE_DIM にリサイズして JPEG base64 エンコードする。"""
        with Image.open(path) as img:
            img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > MAX_IMAGE_DIM:
                scale = MAX_IMAGE_DIM / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _call_api(self, b64: str) -> str:
        """Vision LLM API を呼び出してテキストレスポンスを返す。
        Qwen3 の thinking mode を有効化して推論精度を向上させる。
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": LIMB_CHECK_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            temperature=0.2,
            max_tokens=1024,  # input=2561tokens, 4096-2561=1535available → thinking無効化でJSON確保
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return response.choices[0].message.content or ""

    def _call_api_pass2(self, b64: str) -> str:
        """Pass 2 用の Vision LLM API 呼び出し（短いプロンプト・長い thinking 枠）。"""
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": LIMB_CHECK_PROMPT_PASS2},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            temperature=0.2,
            max_tokens=1536,  # Pass 2 はプロンプトが短い分 thinking に余裕を持たせる
            extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _parse_response(raw: str, path: str, filter_pats=None) -> LimbCheckResult:
        """
        LLM のレスポンスから JSON を抽出してパースする。
        Qwen3 thinking mode の <think>...</think> ブロックを除去してからパース。
        パース失敗時は ok=True（見逃し方向に倒す）。
        """
        # Qwen3 thinking ブロックを除去
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

        # ```json ... ``` などのマークダウン装飾を除去
        text = re.sub(r"```[a-z]*", "", text).strip("`").strip()

        # 最初の { ... } ブロックを抽出
        # 貪欲マッチで最初の { ～ 最後の } を取る
        # ※ 非貪欲 \{.*?\} だと issues 配列内の } で切れてしまう
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            logger.warning(f"JSON 抽出失敗 ({Path(path).name}): {raw[:120]}")
            return LimbCheckResult(path=path, ok=True, error=f"parse error: {raw[:80]}")

        try:
            data = json.loads(m.group())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON デコード失敗 ({Path(path).name}): {e}")
            return LimbCheckResult(path=path, ok=True, error=f"json error: {e}")

        try:
            confidence = float(data.get("confidence", 1.0))
        except (ValueError, TypeError):
            confidence = 1.0

        # filter_pats が指定されていない場合はデフォルトの Pass 1 フィルターを使用
        if filter_pats is None:
            filter_pats = _FILTER_PATS_PASS1

        issues = [
            iss for iss in data.get("issues", [])
            if not any(p.search(iss) for p in filter_pats)
            or any(p.search(iss) for p in _BODY_FUSION_PRESERVE_PATS)  # TP保護: 真の融合語があれば復活
        ]
        ok = bool(data.get("ok", True)) or len(issues) == 0

        return LimbCheckResult(
            path=path,
            ok=ok,
            issues=issues,
            confidence=confidence,
        )
