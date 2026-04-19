# paperspace-automation

## 概要
Paperspace 無料GPU 上の Stable Diffusion WebUI を 24時間無人運転するための自動化スクリプト群。

| コンポーネント | 役割 |
|---|---|
| `startup.sh` | Paperspace VM 起動時に自動実行。venv/WebUI/cloudflared を起動 |
| `auto_gen_worker.py` | Notion DB をキューとして画像生成を実行、Google Drive へ保存 |
| `api_gravity_template.py` | WebUI カスタム API エンドポイント (gravity) |
| `.github/workflows/paperspace-watchdog.yml` | GitHub Actions Cron で Notebook 停止を検知＆再起動 |
| `.github/scripts/paperspace_watchdog.py` | watchdog ロジック本体 |

## フェーズ
- Phase 1 (完了): `参考記事/webui2 (4).ipynb` — 手動 Run-All で 2〜3分復帰
- Phase 2a: GitHub Actions Watchdog で自動再起動
- Phase 2b: startup.sh で VM 起動と同時に WebUI を自動立ち上げ
- Phase 2c: Notion キューで画像生成を自動化

## 状態
開発中 (Phase 2 実装完了・Spike 検証待ち)

## 技術
Python / Bash / GitHub Actions / Paperspace API / Notion API / rclone / Cloudflare Tunnel

## セットアップ
`README.md` を参照。
