"""ComfyUI 远程调用工具 API：GET /remote_call/outputs?prompt_id=xxx"""
from __future__ import annotations

from urllib.parse import urlencode

import server

from .nodes import _query_outputs_cache


def _base_url(request) -> str:
    host = request.headers.get("Host", "127.0.0.1:8188")
    scheme = getattr(request.url, "scheme", "http") if hasattr(request, "url") else "http"
    return f"{scheme}://{host}"


@server.PromptServer.instance.routes.get("/remote_call/outputs")
async def get_remote_call_outputs(request):
    prompt_id = request.rel_url.query.get("prompt_id", "").strip()
    if not prompt_id:
        return server.web.json_response({"ok": False, "prompt_id": "", "outputs": [], "message": "缺少 prompt_id 参数"}, status=400)

    refs = _query_outputs_cache.get(prompt_id, [])
    base = _base_url(request)
    outputs = [
        {
            "filename": r["filename"],
            "subfolder": r.get("subfolder", "remote_call"),
            "type": r.get("type", "output"),
            "kind": r.get("kind", "file"),
            "view_url": f"{base}/view?{urlencode({'filename': r['filename'], 'subfolder': r.get('subfolder', 'remote_call'), 'type': r.get('type', 'output')})}",
        }
        for r in refs
    ]
    return server.web.json_response({"ok": True, "prompt_id": prompt_id, "outputs": outputs, "message": f"共 {len(outputs)} 个输出"})
