"""
Google Drive アップロードユーティリティ。
OAuth2 ユーザー認証（リフレッシュトークン）を使い、マイドライブに画像をアップロードする。

環境変数:
  GDRIVE_TOKEN          : token.json のパス（デフォルト: /storage/paperspace-automation/gdrive_token.json）
  GDRIVE_ROOT_FOLDER_ID : アップロード先のルートフォルダ ID（Drive の outputs フォルダ）
"""

import base64
import io
import logging
import os
import time
from pathlib import Path

def _default_token_path() -> str:
    local = Path(__file__).parent / "gdrive_token.json"
    if local.exists():
        return str(local)
    return "/storage/paperspace-automation/gdrive_token.json"

TOKEN_PATH = os.environ.get("GDRIVE_TOKEN") or _default_token_path()
GDRIVE_ROOT_FOLDER_ID = os.environ.get("GDRIVE_ROOT_FOLDER_ID", "")
SCOPES = ["https://www.googleapis.com/auth/drive"]

_service = None
_folder_cache: dict[tuple, str] = {}


def _get_service():
    global _service
    if _service is not None:
        return _service
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "google-api-python-client / google-auth が未インストールです。\n"
            "pip install google-api-python-client google-auth を実行してください。"
        )
    if not Path(TOKEN_PATH).exists():
        raise FileNotFoundError(f"トークンが見つかりません: {TOKEN_PATH}\nローカルで auth_gdrive.py を実行してトークンを生成してください。")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # 更新されたトークンを保存（バックアップ付き）
            bak = TOKEN_PATH + ".bak"
            try:
                import shutil
                shutil.copy2(TOKEN_PATH, bak)
            except Exception:
                pass
            Path(TOKEN_PATH).write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "トークンが無効または期限切れです。ローカルで auth_gdrive.py を再実行してください。"
            )
    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _get_or_create_folder(name: str, parent_id: str) -> str:
    service = _get_service()
    safe_name = name.replace("'", "\\'")
    q = (
        f"name='{safe_name}' and '{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    res = service.files().list(q=q, fields="files(id)", spaces="drive").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def _resolve_folder_path(rel_path: str, root_folder_id: str) -> str:
    parts = [p for p in Path(rel_path).parts if p]
    current_id = root_folder_id
    for i, part in enumerate(parts):
        cache_key = (root_folder_id, *parts[: i + 1])
        if cache_key in _folder_cache:
            current_id = _folder_cache[cache_key]
        else:
            current_id = _get_or_create_folder(part, current_id)
            _folder_cache[cache_key] = current_id
    return current_id


def _upload_with_retry(service, meta: dict, image_bytes: bytes, max_retry: int = 5):
    from googleapiclient.http import MediaIoBaseUpload
    for attempt in range(max_retry):
        try:
            fh = io.BytesIO(image_bytes)
            media = MediaIoBaseUpload(fh, mimetype="image/png", resumable=True)
            service.files().create(body=meta, media_body=media, fields="id").execute()
            return
        except Exception as e:
            if attempt == max_retry - 1:
                raise
            wait = 2 ** attempt
            logging.warning(f"Drive アップロードリトライ ({attempt+1}/{max_retry}): {e} — {wait}s 待機")
            time.sleep(wait)


def download_config_from_drive(filename: str, dest_path: str, parent_folder_id: str = "") -> bool:
    """Drive 上の設定ファイルを名前で検索し、ローカルにダウンロードする。"""
    try:
        service = _get_service()
        safe = filename.replace("'", "\\'")
        q = f"name='{safe}' and trashed=false"
        if parent_folder_id:
            q += f" and '{parent_folder_id}' in parents"
        res = service.files().list(
            q=q, fields="files(id,modifiedTime)",
            orderBy="modifiedTime desc", pageSize=1,
        ).execute()
        files = res.get("files", [])
        if not files:
            return False
        content = service.files().get_media(fileId=files[0]["id"]).execute()
        Path(dest_path).write_bytes(content)
        return True
    except Exception as e:
        logging.warning(f"Drive からの {filename} ダウンロード失敗: {e}")
        return False


def upload_images_to_drive(
    images_b64: list,
    outpath_samples: str,
    root_folder_id: str = "",
    filename_prefix: str = "",
) -> bool:
    """
    生成画像（base64 リスト）を Drive にアップロードする。

    outpath_samples: "outputs/000_Original_.../008_葉山_陽菜_Original/2026-04-23_..."
                     先頭の "outputs/" は除去してルートフォルダ直下に配置する。
    root_folder_id:  省略時は環境変数 GDRIVE_ROOT_FOLDER_ID を使用。
    filename_prefix: ファイル名のプレフィックス（省略時はタイムスタンプ）。
    戻り値: 全件成功で True、1件でも失敗で False。
    """
    if not images_b64:
        return True

    fid = root_folder_id or GDRIVE_ROOT_FOLDER_ID
    if not fid:
        logging.error("GDRIVE_ROOT_FOLDER_ID が設定されていません。Drive アップロードをスキップ。")
        return False

    rel = outpath_samples.strip().strip("\"'")
    if rel.startswith("outputs/") or rel.startswith("outputs\\"):
        rel = rel[len("outputs/"):]

    try:
        service = _get_service()
        folder_id = _resolve_folder_path(rel, fid)
        prefix = filename_prefix or str(int(time.time()))
        all_ok = True
        for idx, img_b64 in enumerate(images_b64, 1):
            try:
                img_bytes = base64.b64decode(img_b64)
                fname = f"{prefix}_{idx:04d}.png"
                meta = {"name": fname, "parents": [folder_id]}
                _upload_with_retry(service, meta, img_bytes)
                print(f"  ☁️  Drive: {rel}/{fname}")
            except Exception as e:
                logging.error(f"  Drive アップロード失敗 [{idx}]: {e}")
                all_ok = False
        return all_ok
    except Exception as e:
        logging.error(f"  Drive アップロード全体エラー: {e}")
        return False
