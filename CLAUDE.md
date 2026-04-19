# AI for GOOD — 仮想組織エージェントシステム

## 会社情報
- **会社名**: AI for GOOD / **ミッション**: 最高の世界へ
- **事業内容**: 人々のあらゆるペインを取り除き、幸福にする
- **CEO**: 課題発見→アプローチ→実装→実施→評価→改善のサイクルを統括

## ブランドルール
**トーン**: 温かく誠実、寄り添う。「です・ます」調基本（社内は「だ・である」可）。一人称「私たち」「AI for GOOD」（「弊社」「AFG」不可）。

**禁止**: 誇大表現（世界一・革命的など根拠なし）、競合攻撃、差別表現、説明なし専門用語。否定形は「〜の代わりに」でリフレーム。

**用語統一**: ユーザー/課題/改善/AI for GOOD/ペイン（「問題」「修正」「弊社」「苦痛」は使わない）

## UI・デザイン規約

**UI（`.tsx` `.svelte` `.css` `.html` `.vue` 等）を作成・追加・変更・レビューする際は必ず `DESIGN.md` を Read してから実装すること。**

- 色・フォント・間隔・コンポーネントルールはすべて `DESIGN.md` が唯一の正とする
- 日本語UI固有のCSS（line-break / word-break / font-family等）は `DESIGN.md` Section 5 を参照
- Tailwind / CSS-in-JS を使う場合も `DESIGN.md` の値に従う
- UIレビューは `/design-review` スキルを使う

## コンテキスト最適化ルール

**無駄な読み込みを避け、コンテキストを節約する。**

### ファイル読み込み
- **Read の前に Grep** — 目的の関数・変数・行が分かっている場合は先に Grep で特定してから Read
- **Read は必要な範囲だけ** — 大きいファイルは `offset` + `limit` で必要な行だけ読む
- **構造把握に Glob 乱用しない** — `**/*` Glob は遅く重い。目的のファイルパスが推測できる場合は直接 Read

### Bash 出力
- **rtk が使える場合は `rtk <command>` で実行** — pip install / npm install / cargo build / git log など長い出力が予想されるコマンドは `rtk` でラップしてトークン圧縮する（例: `rtk pip install -r requirements.txt`）
- **rtk が使えない場合は `| head -30` を付ける** — 長大な出力は必ずトリミング
- **npm 情報は特定バージョンで取得** — `npm view expo versions --json` ではなく `npm view expo@54.0.33` のように特定バージョンを直接指定
- **エラーログは最初の20行で判断** — 同じエラーが繰り返されるログ全体を読まない

### 調査方針
- **仮説を立ててから調査** — 「原因はXのはず」→ その箇所だけ確認。広範囲を手当たり次第に読まない
- **1ファイルで分かる場合は1ファイルだけ** — 関連しそうという理由で複数ファイルを同時 Read しない

## ルーティング

**エージェント委譲・連携パターンが必要な場合は `.claude/ROUTING.md` を Read してから実行すること。**

**判断に迷ったら**: まず strategy-advisor に相談しタスクを分解、サブタスクごとに適切なエージェントを起動する。

### レビューゲート

**デフォルト: レビューする。** `output/<部門>/` に成果物を保存した場合、対応レビュアーを**必ず**起動する。省略にはCEOの明示的な「レビュー不要」指示が必要。

**「レビューして」と言われたら**直前の成果物を自動特定し、種別に応じたスキル/エージェントを起動する:

| 成果物の種別 | 使うスキル/エージェント |
|-------------|----------------------|
| コード・実装 | `/review` |
| 実装計画・設計書 | `/plan-eng-review` |
| 記事・コンテンツ | `/editor-pro` → content-editor |
| 画像・UI/デザイン | `/design-review` |
| 同人誌スクリプトJSON | `/script-reviewer` |
| 戦略・事業計画 | strategy-reviewer |
| 調査レポート | research-reviewer |
| 種別不明 | CEOに確認 |

**部門→レビュアー**: strategy/→strategy-reviewer, research/→research-reviewer, product/→product-reviewer, marketing/→marketing-reviewer, content/→content-editor, operations/→operations-reviewer, data/→data-reviewer, security/→security-reviewer, finance/→finance-reviewer

判定: ✅承認→次工程 / ⚠️条件付き承認→修正しつつ次工程 / ❌差し戻し→修正後再審査

**省略可（CEO明示不要）**: 単発調査・内部草案・CEOが即確認する前提のタスク

### 承認フロー・エスカレーション

**CEOに判断を仰ぐ**: Go/No-Go判断 / フェーズ移行 / 予算超過120%以上 / 方針の大幅逸脱

**エスカレーション条件**: 情報不足 / 部門間対立 / 想定外の事態 / スキル外判断
→報告形式: 「状況→影響→選択肢→推奨案」

## 成果物管理

`output/{strategy|research|product|marketing|content|operations|data|security|finance}/`
ファイル名: `YYYY-MM-DD_エージェント名_概要.md`。大きな成果物はサブフォルダ可。

| 保存先 | 用途 | 形式 |
|--------|------|------|
| output/<部門>/ | 公式成果物（レビュー対象） | Markdown |
| research/ | researchスキルのレポート | HTML |
| knowledge/ | knowledgeスキルのナレッジ | HTML |

## 活動ログ（Notion記録）

**サブエージェント（Task起動）はMCPツール不可。メイン会話が代行記録する。**

DB ID: `32c88bba-289a-80f9-8edf-fa1f31dae136`

- サブエージェント起動時に「進行中」で記録 → 完了時に「完了」へ更新
- **大きなマイルストーン完了時も必ず記録**（担当者: `Claude Code`）

プロパティ: 名前(title) / 担当者(select=エージェント名) / 部門(select=9部門) / タスク(rich_text) / ステータス(select: 未着手/進行中/完了/保留) / 品質レベル(select: 最高/高/中/低) / 日時(date: ISO 8601+09:00)

## ナレッジ重複防止

**market-researcher / tech-scout / competitor-analyst を起動する前に、`knowledge/` と `output/research/` に同一トピックの既存調査がないか必ず確認する。** 重複が見つかった場合は差分調査のみ実施し、既存ファイルを更新する。

## プロジェクトレジストリ

詳細は `.claude/PROJECTS.md` を Read すること。

**プロジェクト特定**: 指示にプロジェクト名があれば `.claude/PROJECTS.md` から特定→PROJECT.md読込。不明ならCEOに確認。
**新規立ち上げ**: フォルダ作成→PROJECT.md作成→`.claude/PROJECTS.md` の一覧に追記。
