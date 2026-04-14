"""マンガ生成パイプライン CLIエントリポイント。

使用方法:
  # script.json + 画像 → マンガページ生成 (Phase A: compose only)
  python manga_main.py --input script.json

  # SD画像生成 → ページ合成 (Phase B: full pipeline)
  python manga_main.py --input script.json --generate

  # レジューム: 生成済みパネルをスキップ
  python manga_main.py --input script.json --generate --resume

  # 画像生成のみ（ページ合成なし）
  python manga_main.py --input script.json --generate --skip-compose

  # アップスケール付き
  python manga_main.py --input script.json --generate --upscale

  # カスタム設定ファイル
  python manga_main.py --input script.json --config config.yaml

  # 出力ディレクトリ指定
  python manga_main.py --input script.json --output ./output/manga

  # キャラCSVからプロンプトを読み込み
  python manga_main.py --input script.json --generate \
    --chara-csv "Chara details - Chara Details.csv" \
    --chara-name "Natsumi Asahina"
"""

import argparse
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
from copy import deepcopy
from pathlib import Path

import yaml

from modules.manga.manga_script import MangaScript
from modules.manga.page_composer import PageComposer


def load_config(config_path: str) -> dict:
    """YAML設定ファイルを読み込む。"""
    p = Path(config_path)
    if not p.exists():
        print(f"Warning: Config file not found: {config_path}, using defaults")
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_manga_config(config: dict) -> dict:
    """設定辞書からmangaセクションを取得（デフォルト値付き）。"""
    manga = config.get("manga", {})

    # デフォルト値の設定
    defaults = {
        "output_dir": "./output/manga",
        "prompts": {
            "quality_prefix": "ultra masterpiece, best quality, very aesthetic",
            "style_tags": "colored manga panel, anime style, clean line art",
            "negative_base": "worst quality, low quality, text, watermark, monochrome",
            "camera_map": {
                "close_up": "close up, face focus, upper body",
                "medium": "medium shot, cowboy shot",
                "full_body": "full body shot",
                "wide": "wide shot, establishing shot, background focus",
            },
        },
        "layout": {
            "size_preset": "pixiv",
            "presets": {
                "pixiv": {"width": 1700, "height": 2400, "dpi": 144},
                "print_b5": {"width": 4212, "height": 5952, "dpi": 600},
                "print_b5_color": {"width": 2106, "height": 2976, "dpi": 300},
                "webtoon": {"width": 800, "height": 1280, "dpi": 72},
            },
            "page_width": 1700,
            "page_height": 2400,
            "gutter": 20,
            "margin": 40,
            "border_width": 3,
            "reading_order": "rtl",
            "bg_color": "white",
            "border_color": "black",
        },
        "composer": {
            "font_path": None,
            "font_size": 28,
            "text_direction": "vertical",
            "bubble_padding": 15,
            "bubble_fill_color": "white",
            "bubble_outline_color": "black",
            "bubble_outline_width": 2,
            "bubble_opacity": 230,
        },
    }

    # デフォルトとマージ
    for key, value in defaults.items():
        if key not in manga:
            manga[key] = value
        elif isinstance(value, dict):
            for k, v in value.items():
                if k not in manga[key]:
                    manga[key][k] = v

    return manga


def main():
    parser = argparse.ArgumentParser(
        description="マンガ生成パイプライン - script.jsonからマンガページを生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="MangaScript JSONファイルのパス",
    )
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="設定ファイルのパス (default: config.yaml)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="出力ディレクトリ (default: config.yamlの設定値)",
    )
    parser.add_argument(
        "--image-dir",
        default=None,
        help="コマ画像の基準ディレクトリ（image_pathが相対パスの場合）",
    )
    # --- Phase B flags ---
    parser.add_argument(
        "--generate",
        action="store_true",
        help="SD WebUI APIを使用してパネル画像を生成する",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="既に画像が存在するパネルをスキップして生成を再開する",
    )
    parser.add_argument(
        "--skip-compose",
        action="store_true",
        help="画像生成のみ実行し、ページ合成をスキップする",
    )
    parser.add_argument(
        "--upscale",
        action="store_true",
        help="生成画像をアップスケールする",
    )
    parser.add_argument(
        "--limb-check",
        action="store_true",
        dest="limb_check",
        help="Vision LLM で四肢破綻・体形異常を検出し NG パネルを自動排除する（生成直後・mosaic前）",
    )
    parser.add_argument(
        "--mosaic",
        action="store_true",
        help="パネル画像にモザイクを適用する（アップスケール前）",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="ページ合成後にPDFを自動出力する",
    )
    parser.add_argument(
        "--pdf-size",
        default="B5",
        choices=["B5", "A5", "A4", "pixiv"],
        help="PDFページサイズ (default: B5)",
    )
    # --- Character CSV ---
    parser.add_argument(
        "--chara-csv",
        default=None,
        help="キャラクター定義CSVファイルのパス",
    )
    parser.add_argument(
        "--chara-name",
        nargs="+",
        default=None,
        help="使用するキャラクター名（英語名 or 日本語名、複数指定可）",
    )

    args = parser.parse_args()

    # 設定読み込み
    config = load_config(args.config)
    manga_config = get_manga_config(config)

    # 出力ディレクトリの上書き
    output_dir = args.output or manga_config.get("output_dir", "./output/manga")
    manga_config["output_dir"] = output_dir

    # ルートconfigにマージ済みmanga_configを戻す（MangaPipelineが参照するため）
    config["manga"] = manga_config

    # スクリプト読み込み
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    print(f"Loading script: {input_path}")
    script = MangaScript.load(input_path)

    # --- キャラCSV上書き ---
    if args.chara_csv:
        from modules.manga.chara_loader import load_characters_from_csv

        csv_path = Path(args.chara_csv)
        print(f"\nLoading character CSV: {csv_path}")
        csv_chars = load_characters_from_csv(csv_path)
        print(f"  CSV characters loaded: {len(csv_chars)} entries")

        # スクリプトのキャラを CSV データで上書き
        # 重要: deepcopy で共有参照を断ち切る。csv_chars 辞書の値は
        # 複数キー（英語名・日本語名）で同一オブジェクトを参照しているため、
        # name を書き換えると他のキーからの参照にも影響する。
        updated = 0
        for i, sc in enumerate(script.characters):
            csv_char = csv_chars.get(sc.name)
            if csv_char:
                script.characters[i] = deepcopy(csv_char)
                # スクリプト上のキャラ名を維持
                script.characters[i].name = sc.name
                updated += 1
                print(f"    [OK] {sc.name} -> CSV prompts applied")
            else:
                print(f"    [--] {sc.name} -> not found in CSV, keeping original")

        # --chara-name で指定されたキャラがスクリプトに存在しない場合は追加
        if args.chara_name:
            for cname in args.chara_name:
                if not script.get_character(cname):
                    csv_char = csv_chars.get(cname)
                    if csv_char:
                        char_copy = deepcopy(csv_char)
                        char_copy.name = cname
                        script.characters.append(char_copy)
                        updated += 1
                        print(f"    [+] {cname} -> added from CSV")
                    else:
                        print(f"    [!] {cname} -> not found in CSV")

        print(f"  Updated {updated} character(s)")

    print(f"\n  Title: {script.title}")
    print(f"  Characters: {len(script.characters)}")
    print(f"  Pages: {len(script.pages)}")
    print(f"  Total panels: {script.total_panels}")

    # --- Phase B: フルパイプライン ---
    if args.generate:
        from modules.manga.manga_pipeline import MangaPipeline

        pipeline = MangaPipeline(config)
        pipeline.run(
            script,
            script_path=input_path,
            do_generate=True,
            do_limb_check=args.limb_check,
            do_mosaic=args.mosaic,
            do_upscale=args.upscale,
            do_compose=not args.skip_compose,
            do_pdf=args.pdf,
            pdf_size=args.pdf_size,
            resume=args.resume,
        )
        return

    # --- Phase A: ページ合成のみ（既存動作を維持） ---
    pages_dir = Path(output_dir) / "pages"
    print(f"\nComposing pages...")
    composer = PageComposer(manga_config)
    saved = composer.compose_all(script, pages_dir, args.image_dir)

    print(f"\nDone! {len(saved)} pages saved to: {pages_dir}")

    # PDF出力（Phase A）
    if args.pdf:
        import re
        from modules.manga.pdf_exporter import export_pdf

        title = getattr(script, 'title', 'manga') or 'manga'
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title).strip('. ')
        pdf_path = Path(output_dir) / f"{safe_title}_doujinshi.pdf"
        print(f"\nExporting PDF...")
        export_pdf(
            pages_dir=pages_dir,
            output_path=pdf_path,
            page_size=args.pdf_size,
        )
        print(f"PDF saved: {pdf_path}")


if __name__ == "__main__":
    main()
