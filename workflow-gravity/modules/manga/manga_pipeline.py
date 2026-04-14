"""マンガ画像生成パイプライン。

prompt_builder で構築したプロンプトを SDClient で画像化し、
スクリプトにパスを書き戻し、最終的にページ合成を行う。
"""

import time
from pathlib import Path

from .manga_script import MangaScript
from .prompt_builder import PanelPromptResult, build_all_prompts
from .script_validator import ScriptValidator


class MangaPipeline:
    """マンガ生成パイプラインのオーケストレーター。"""

    def __init__(self, config: dict):
        """
        Args:
            config: ルート設定辞書全体 (generation, manga, upscale セクションを含む)
        """
        self.config = config
        self.gen_config = config.get("generation", {})
        self.manga_config = config.get("manga", {})
        self.prompt_config = self.manga_config.get("prompts", {})
        self.upscale_config = config.get("upscale", {})
        self.mosaic_config = config.get("mosaic", {})

        # output directories
        manga_output = self.manga_config.get("output_dir", "./output/manga")
        self.output_base = Path(manga_output)
        self.panel_raw_dir = self.output_base / "panel_raw"
        self.panel_mosaic_dir = self.output_base / "panel_mosaic"
        self.panel_upscaled_dir = self.output_base / "panel_upscaled"
        self.pages_dir = self.output_base / "pages"

    def run(
        self,
        script: MangaScript,
        script_path: Path,
        *,
        do_generate: bool = True,
        do_limb_check: bool = False,
        do_mosaic: bool = False,
        do_upscale: bool = False,
        do_compose: bool = True,
        do_pdf: bool = False,
        pdf_size: str = "B5",
        resume: bool = False,
    ) -> MangaScript:
        """パイプライン全体を実行する。

        Args:
            script: 入力 MangaScript
            script_path: チェックポイント保存先 (script.json)
            do_generate: SD画像生成を実行するか
            do_mosaic: モザイクを適用するか（アップスケール前）
            do_upscale: アップスケールを実行するか
            do_compose: ページ合成を実行するか
            do_pdf: PDF出力を実行するか
            pdf_size: PDFページサイズ
            resume: True の場合、image_path が既に設定済みのパネルをスキップ

        Returns:
            更新された MangaScript (image_path が設定済み)
        """
        start_time = time.time()
        total_steps = 4 + (1 if do_limb_check else 0) + (1 if do_mosaic else 0) + (1 if do_pdf else 0)
        step = 0

        print("=" * 60)
        print("  Manga Pipeline - Phase B")
        print("=" * 60)

        # --- Step 0: Validate script ---
        print(f"\n[Step 0/{total_steps}] Validating script...")
        validator = ScriptValidator(strict=False)
        validator.validate(script)
        validator.log_issues()
        print(f"  Validation complete: {len(validator.issues)} issue(s) found")

        # --- Step 1: Build prompts ---
        step += 1
        print(f"\n[Step {step}/{total_steps}] Building prompts for {script.total_panels} panels...")

        # 焼き込み済みプロンプトがあればそのまま使用（rebuild不要）
        all_baked = all(
            panel.is_baked
            for page in script.pages
            for panel in page.panels
        )

        if all_baked:
            print(f"  Using pre-baked prompts from script JSON")
            prompt_results = []
            for page in script.pages:
                for panel in page.panels:
                    pos_esc = panel.positive_prompt.replace('"', '\\"')
                    neg_esc = panel.negative_prompt.replace('"', '\\"')
                    prompt_line = (
                        f'--prompt "{pos_esc}" '
                        f'--negative_prompt "{neg_esc}" --seed -1'
                    )
                    prompt_results.append(PanelPromptResult(
                        panel_id=panel.panel_id,
                        prompt_line=prompt_line,
                        width=panel.gen_width,
                        height=panel.gen_height,
                        page_number=page.page_number,
                    ))
        else:
            prompt_results = build_all_prompts(script, self.prompt_config)

        print(f"  Built {len(prompt_results)} prompt(s)")

        # プロンプトダンプ（検証用: pose_libraryタグ・キャラタグの反映確認）
        try:
            dump_path = self.output_base / "prompt_dump.txt"
            with open(dump_path, "w", encoding="utf-8") as df:
                for pr in prompt_results:
                    df.write(f"=== Panel {pr.panel_id} (Page {pr.page_number}, {pr.width}x{pr.height}) ===\n")
                    df.write(f"{pr.prompt_line}\n\n")
            print(f"  Prompt dump saved: {dump_path}")
        except Exception:
            pass

        # --- Step 2: Generate images ---
        step += 1
        if do_generate:
            print(f"\n[Step {step}/{total_steps}] Generating panel images...")
            self._generate_panels(script, script_path, prompt_results, resume)
        else:
            print(f"\n[Step {step}/{total_steps}] Skipping generation")

        # --- Step 2.5: Limb check (optional) ---
        if do_limb_check:
            step += 1
            print(f"\n[Step {step}/{total_steps}] Limb-checking panels...")
            self._limb_check_panels(script, script_path)

        # --- Step 3: Apply mosaic (optional) ---
        if do_mosaic:
            step += 1
            print(f"\n[Step {step}/{total_steps}] Applying mosaic to panels...")
            self._apply_mosaic(script, script_path)

        # --- Step 3: Upscale ---
        step += 1
        if do_upscale:
            print(f"\n[Step {step}/{total_steps}] Upscaling panel images...")
            self._upscale_panels(script, script_path)
        else:
            print(f"\n[Step {step}/{total_steps}] Skipping upscale")

        # --- Step 4: Compose pages ---
        step += 1
        if do_compose:
            print(f"\n[Step {step}/{total_steps}] Composing manga pages...")
            self._compose_pages(script)
        else:
            print(f"\n[Step {step}/{total_steps}] Skipping page composition")

        # --- Step 5: Export PDF (optional) ---
        if do_pdf:
            step += 1
            print(f"\n[Step {step}/{total_steps}] Exporting PDF...")
            self._export_pdf(script, pdf_size)

        elapsed = time.time() - start_time
        print(f"\n{'=' * 60}")
        print(f"  Pipeline complete! ({elapsed:.1f}s)")
        print(f"  Output: {self.output_base.resolve()}")
        print(f"{'=' * 60}")

        return script

    # ------------------------------------------------------------------
    # Image selection: pick the sharpest candidate (Laplacian variance)
    # ------------------------------------------------------------------

    @staticmethod
    def _select_best_candidate(paths: list[Path]) -> Path:
        """候補画像からシャープネス最高のものを選択する。

        Laplacian分散をシャープネスのプロキシとして使用。
        OpenCVが利用できない場合はファイルサイズで代替。
        """
        if len(paths) == 1:
            return paths[0]

        try:
            import cv2
            import numpy as np  # noqa: F401 (cv2 dependency)

            best_path = paths[0]
            best_score = -1.0

            for p in paths:
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                score = cv2.Laplacian(img, cv2.CV_64F).var()
                if score > best_score:
                    best_score = score
                    best_path = p

            return best_path
        except ImportError:
            # OpenCV未インストール時: ファイルサイズ最大を選択
            return max(paths, key=lambda p: p.stat().st_size)

    def _generate_panels(
        self,
        script: MangaScript,
        script_path: Path,
        prompt_results: list[PanelPromptResult],
        resume: bool,
    ) -> None:
        """全パネルの画像を生成する。

        各パネルに固有の解像度があるため、SDClient.txt2img() を
        パネルごとに呼び出し、呼び出し前に payload_template の
        width/height を一時的にオーバーライドする。

        candidates > 1 の場合、各パネルでN回生成し最良画像を選択する。
        """
        from modules.sd_client import SDClient

        client = SDClient(self.gen_config)

        if not client.check_connection():
            print("  ERROR: Cannot connect to SD WebUI API. Aborting generation.")
            return

        self.panel_raw_dir.mkdir(parents=True, exist_ok=True)

        # 候補数（config.yaml の manga.sd.candidates、デフォルト1）
        candidates_count = self.manga_config.get("sd", {}).get("candidates", 1)
        if candidates_count > 1:
            print(f"  Candidate selection: {candidates_count} images per panel (pick sharpest)")

        # (page_number, panel_id) -> Panel オブジェクトへの参照マップ
        panel_map: dict[tuple[int, int], object] = {}
        for page in script.pages:
            for panel in page.panels:
                panel_map[(page.page_number, panel.panel_id)] = panel

        generated_count = 0
        skipped_count = 0
        failed_count = 0

        for i, pr in enumerate(prompt_results):
            panel = panel_map[(pr.page_number, pr.panel_id)]
            # ページ番号×100 + panel_id でユニークなファイルインデックスを生成
            file_index = pr.page_number * 100 + pr.panel_id

            # --- Resume check ---
            if resume and panel.image_path:
                img_file = Path(panel.image_path)
                if img_file.exists():
                    skipped_count += 1
                    print(f"  [SKIP] Page {pr.page_number} Panel {pr.panel_id} (image exists)")
                    continue

            label = (f"  [{i + 1}/{len(prompt_results)}] Generating panel {pr.panel_id} "
                     f"(page {pr.page_number}, {pr.width}x{pr.height}"
                     f"{f', {candidates_count} candidates' if candidates_count > 1 else ''})")
            print(label + "...")

            # --- Temporarily override width/height and clear base prompts in SDClient ---
            original_w = client.payload_template.get("width")
            original_h = client.payload_template.get("height")
            original_prompt = client.payload_template.get("prompt")
            original_neg_prompt = client.payload_template.get("negative_prompt")

            client.payload_template["width"] = pr.width
            client.payload_template["height"] = pr.height
            # manga用のプロンプトはコマンドライン形式(--prompt)で直接渡されるため、
            # ベースのテンプレートにあるprompt(白黒設定など)が混ざらないように一時的に消去
            client.payload_template.pop("prompt", None)
            client.payload_template.pop("negative_prompt", None)

            try:
                if candidates_count <= 1:
                    # --- 単一生成（従来パス） ---
                    saved_count, _ = client.txt2img(
                        pr.prompt_line,
                        self.panel_raw_dir,
                        start_index=file_index,
                    )

                    if saved_count > 0:
                        generated_files = list(
                            self.panel_raw_dir.glob(f"{file_index:05d}-*.png")
                        )
                        if generated_files:
                            chosen_path = max(generated_files, key=lambda p: p.stat().st_mtime)
                            panel.image_path = str(chosen_path)
                            generated_count += 1
                            print(f"    -> {chosen_path.name}")
                        else:
                            print(f"  WARNING: Image saved but not found for panel {pr.panel_id}")
                            failed_count += 1
                    else:
                        print(f"  WARNING: Generation failed for panel {pr.panel_id}")
                        failed_count += 1
                else:
                    # --- 複数候補生成: N回呼び出し、最良画像を選択 ---
                    candidate_paths: list[Path] = []
                    for c_idx in range(candidates_count):
                        # 候補ごとにユニークなインデックス: file_index * 10 + c_idx
                        c_file_index = file_index * 10 + c_idx
                        saved, _ = client.txt2img(
                            pr.prompt_line,
                            self.panel_raw_dir,
                            start_index=c_file_index,
                        )
                        if saved > 0:
                            c_files = list(
                                self.panel_raw_dir.glob(f"{c_file_index:05d}-*.png")
                            )
                            if c_files:
                                candidate_paths.append(
                                    max(c_files, key=lambda p: p.stat().st_mtime)
                                )

                    if candidate_paths:
                        chosen = self._select_best_candidate(candidate_paths)
                        # 最良画像をオリジナルの file_index でリネーム
                        final_name = f"{file_index:05d}-{chosen.stem.split('-', 1)[-1]}.png"
                        final_path = self.panel_raw_dir / final_name
                        if chosen != final_path:
                            chosen.rename(final_path)
                        panel.image_path = str(final_path)
                        generated_count += 1
                        # 不採用候補を削除
                        for cp in candidate_paths:
                            if cp != chosen and cp.exists():
                                cp.unlink(missing_ok=True)
                        print(f"    -> Selected best of {len(candidate_paths)}: {final_path.name}")
                    else:
                        print(f"  WARNING: All {candidates_count} candidates failed for panel {pr.panel_id}")
                        failed_count += 1

            finally:
                # --- Restore original width/height and base prompts ---
                if original_w is not None:
                    client.payload_template["width"] = original_w
                else:
                    client.payload_template.pop("width", None)
                if original_h is not None:
                    client.payload_template["height"] = original_h
                else:
                    client.payload_template.pop("height", None)
                if original_prompt is not None:
                    client.payload_template["prompt"] = original_prompt
                if original_neg_prompt is not None:
                    client.payload_template["negative_prompt"] = original_neg_prompt

            # --- Checkpoint: save script after each panel ---
            script.save(script_path)

        print(f"  Generation complete: {generated_count} generated, "
              f"{skipped_count} skipped, {failed_count} failed")

    def _limb_check_panels(
        self,
        script,
        script_path: Path,
    ) -> None:
        """Vision LLM で四肢破綻・体形異常を検出し NG パネルを排除する。

        NG 判定されたパネルは panel_raw/limb_ng/ へ移動し、
        image_path を None にすることで後続の mosaic/upscale/compose をスキップさせる。
        """
        import shutil
        from modules.vision_limb_checker import VisionLimbChecker

        checker = VisionLimbChecker()
        ng_dir = self.panel_raw_dir / "limb_ng"
        ng_dir.mkdir(parents=True, exist_ok=True)

        ng_count = 0
        ok_count = 0

        for page in script.pages:
            for panel in page.panels:
                if not panel.image_path:
                    continue
                img_path = Path(panel.image_path)
                if not img_path.exists():
                    continue

                result = checker.check(img_path)
                if result.ok:
                    ok_count += 1
                else:
                    ng_path = ng_dir / img_path.name
                    shutil.move(str(img_path), str(ng_path))
                    panel.image_path = None
                    ng_count += 1
                    issues_str = " / ".join(result.issues) if result.issues else "unknown"
                    print(f"  [NG] Panel {panel.panel_id} (page {page.page_number}): {issues_str}")

        script.save(script_path)
        print(f"  Limb check: {ok_count} OK, {ng_count} rejected → panel_raw/limb_ng/")

    def _upscale_panels(
        self,
        script: MangaScript,
        script_path: Path,
    ) -> None:
        """全パネル画像をアップスケールし、image_path を更新する。"""
        from modules.upscaler import UpscalerFactory

        sd_url = self.gen_config.get("sd_url")
        upscaler = UpscalerFactory(self.upscale_config, sd_url=sd_url)

        self.panel_upscaled_dir.mkdir(parents=True, exist_ok=True)

        upscaled_count = 0

        for page in script.pages:
            for panel in page.panels:
                if not panel.image_path:
                    continue

                raw_path = Path(panel.image_path)
                if not raw_path.exists():
                    print(f"  [SKIP] Panel {panel.panel_id}: raw image not found")
                    continue

                upscaled_path = self.panel_upscaled_dir / raw_path.name

                # Resume: skip if already upscaled
                if upscaled_path.exists():
                    panel.image_path = str(upscaled_path)
                    upscaled_count += 1
                    continue

                print(f"  Upscaling panel {panel.panel_id}...")
                success = upscaler.upscale_single(raw_path, upscaled_path)
                if success:
                    panel.image_path = str(upscaled_path)
                    upscaled_count += 1
                else:
                    print(f"  WARNING: Upscale failed for panel {panel.panel_id}")

        # Checkpoint
        script.save(script_path)
        print(f"  Upscale complete: {upscaled_count} panels")

    def _apply_mosaic(
        self,
        script: MangaScript,
        script_path: Path,
    ) -> None:
        """全パネル画像にモザイクを適用する（アップスケール前）。"""
        from modules.auto_mosaic import AutoMosaicPy

        mosaic = AutoMosaicPy(self.mosaic_config)
        self.panel_mosaic_dir.mkdir(parents=True, exist_ok=True)

        processed = 0
        skipped = 0

        for page in script.pages:
            for panel in page.panels:
                if not panel.image_path:
                    continue

                raw_path = Path(panel.image_path)
                if not raw_path.exists():
                    print(f"  [SKIP] Panel {panel.panel_id}: image not found")
                    continue

                mosaic_path = self.panel_mosaic_dir / raw_path.name

                # Resume: 既にモザイク済みならスキップ
                if mosaic_path.exists():
                    panel.image_path = str(mosaic_path)
                    skipped += 1
                    continue

                # process_single: 検出なしでも nomosaic_path にコピー
                mosaic.process_single(
                    input_path=raw_path,
                    output_path=mosaic_path,
                    nomosaic_path=mosaic_path,
                )
                panel.image_path = str(mosaic_path)
                processed += 1

        script.save(script_path)
        print(f"  Mosaic complete: {processed} processed, {skipped} skipped (resume)")

    def _compose_pages(self, script: MangaScript) -> None:
        """マンガページを合成する。"""
        from .page_composer import PageComposer

        composer = PageComposer(self.manga_config)
        saved = composer.compose_all(script, self.pages_dir)
        print(f"  Composed {len(saved)} pages -> {self.pages_dir}")

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """ファイル名に使えない文字を除去する。"""
        import re
        return re.sub(r'[<>:"/\\|?*]', '_', name).strip('. ')

    def _export_pdf(
        self,
        script: MangaScript,
        pdf_size: str = "B5",
    ) -> None:
        """PDF同人誌を出力する。"""
        from .pdf_exporter import export_pdf

        title = getattr(script, 'title', 'manga') or 'manga'
        safe_title = self._sanitize_filename(title)
        pdf_path = self.output_base / f"{safe_title}_doujinshi.pdf"
        export_pdf(
            pages_dir=self.pages_dir,
            output_path=pdf_path,
            page_size=pdf_size,
            reading_order="rtl",
        )
        print(f"  PDF saved: {pdf_path}")
