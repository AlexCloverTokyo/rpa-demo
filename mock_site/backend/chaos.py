import asyncio
import os
import random
from pathlib import Path

import yaml
from fastapi import Request
from fastapi.responses import JSONResponse

CHAOS_CONFIG_PATH = Path(os.environ.get(
    "CHAOS_CONFIG_PATH",
    str(Path(__file__).parent.parent.parent / "chaos_config.yaml"),
))

# mtime-based cache: re-read only when the file changes (Fix #5).
# ファイルのmtimeが変わった時だけ再読み込みし、リクエストごとのディスク読み込みを省く。
_config_cache: dict = {}
_config_mtime: float = 0.0


def _load_config() -> dict:
    global _config_cache, _config_mtime
    if not CHAOS_CONFIG_PATH.exists():
        return {"chaos": {"enabled": False, "rules": []}}
    mtime = CHAOS_CONFIG_PATH.stat().st_mtime
    if mtime != _config_mtime:
        with open(CHAOS_CONFIG_PATH, encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
        _config_mtime = mtime
    return _config_cache


async def chaos_middleware(request: Request, call_next):
    cfg = _load_config()
    if not cfg.get("chaos", {}).get("enabled", False):
        return await call_next(request)

    for rule in cfg.get("chaos", {}).get("rules", []):
        path_match = rule.get("path") == request.url.path
        method_match = rule.get("method", "").upper() == request.method.upper()
        triggered = random.random() < rule.get("probability", 0)
        if path_match and method_match and triggered:
            fault = rule.get("fault")
            if fault == "timeout":
                await asyncio.sleep(10)
            elif fault == "error_500":
                return JSONResponse(
                    status_code=500,
                    content={"detail": "chaos: injected error_500"},
                )
    return await call_next(request)
