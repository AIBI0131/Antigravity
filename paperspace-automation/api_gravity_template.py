import modules.script_callbacks as script_callbacks
import base64
import os
import threading
from collections import deque
from io import BytesIO
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import gradio as gr

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
_GRAVITY_SECRET = os.environ.get("GRAVITY_SECRET", "")
_bearer = HTTPBearer(auto_error=False)

_gravity_lock = threading.Lock()
_gravity_cache = {"images": deque(maxlen=20), "gen_id": 0}


def _check_token(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if _GRAVITY_SECRET and (not creds or creds.credentials != _GRAVITY_SECRET):
        raise HTTPException(status_code=401, detail="unauthorized")


def _safe_resolve(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="forbidden")
    return target


def on_image_saved(params: script_callbacks.ImageSaveParams):
    try:
        if 'grid' in params.filename.lower():
            return
        from PIL import PngImagePlugin
        buffered = BytesIO()
        pnginfo_data = PngImagePlugin.PngInfo()
        if hasattr(params, 'pnginfo') and params.pnginfo:
            for k, v in params.pnginfo.items():
                if isinstance(k, str) and isinstance(v, str):
                    pnginfo_data.add_text(k, v)
        params.image.save(buffered, format="PNG", pnginfo=pnginfo_data)
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        seed = 0
        try:
            basename = os.path.splitext(os.path.basename(params.filename))[0]
            parts = basename.split('-')
            if len(parts) >= 2:
                seed = int(parts[-1])
        except (ValueError, IndexError):
            if hasattr(params, "p") and params.p and hasattr(params.p, "all_seeds") and params.p.all_seeds:
                seed = params.p.all_seeds[0]
        infotext = ""
        if hasattr(params, 'pnginfo') and params.pnginfo:
            infotext = params.pnginfo.get("parameters", "")
        with _gravity_lock:
            _gravity_cache["images"].append({
                "image": img_str, "seed": seed,
                "gen_id": _gravity_cache["gen_id"], "infotext": infotext
            })
    except Exception as e:
        print("gravity_image_cache error:", e)


def on_app_started(demo: gr.Blocks, app: FastAPI):
    route_exists = any(getattr(r, "path", "") == "/gravity/latest_images" for r in app.routes)
    if not route_exists:
        @app.get("/gravity/latest_images")
        def get_latest_images(count: int = 1, gen_id: int = -1, _=Depends(_check_token)):
            with _gravity_lock:
                cache = list(_gravity_cache["images"])
            if gen_id >= 0:
                filtered = [item for item in cache if item["gen_id"] == gen_id]
            else:
                filtered = cache[-count:] if count > 0 else cache
            return {"count": len(filtered),
                    "seeds": [item["seed"] for item in filtered],
                    "infotexts": [item.get("infotext", "") for item in filtered],
                    "gen_id": _gravity_cache["gen_id"]}

        @app.get("/gravity/image/{index}")
        def get_image(index: int, gen_id: int = -1, _=Depends(_check_token)):
            with _gravity_lock:
                cache = list(_gravity_cache["images"])
            if gen_id >= 0:
                filtered = [item for item in cache if item["gen_id"] == gen_id]
            else:
                filtered = cache
            if 0 <= index < len(filtered):
                item = filtered[index]
                return {"image": item["image"], "seed": item["seed"],
                        "infotext": item.get("infotext", "")}
            return JSONResponse(status_code=404, content={"error": f"index {index} not found"})

        @app.post("/gravity/new_generation")
        def new_generation(_=Depends(_check_token)):
            with _gravity_lock:
                _gravity_cache["gen_id"] += 1
            return {"gen_id": _gravity_cache["gen_id"]}

        @app.get("/gravity/list_outputs")
        def list_outputs(_=Depends(_check_token)):
            if not OUTPUTS_DIR.exists():
                return {"files": []}
            files = []
            for f in sorted(OUTPUTS_DIR.rglob("*.png")):
                rel = f.relative_to(OUTPUTS_DIR).as_posix()
                files.append({"path": rel, "size": f.stat().st_size, "mtime": f.stat().st_mtime})
            return {"files": files}

        @app.get("/gravity/download/{file_path:path}")
        def download_output(file_path: str, _=Depends(_check_token)):
            target = _safe_resolve(OUTPUTS_DIR, file_path)
            if not target.exists():
                return JSONResponse(status_code=404, content={"error": "not found"})
            data = target.read_bytes()
            return Response(content=data, media_type="image/png",
                            headers={"Content-Disposition": f"attachment; filename={target.name}"})

        @app.delete("/gravity/delete/{file_path:path}")
        def delete_output(file_path: str, _=Depends(_check_token)):
            target = _safe_resolve(OUTPUTS_DIR, file_path)
            if not target.exists():
                return JSONResponse(status_code=404, content={"error": "not found"})
            target.unlink()
            return {"deleted": file_path}


script_callbacks.on_image_saved(on_image_saved)
script_callbacks.on_app_started(on_app_started)
