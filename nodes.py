from __future__ import annotations

import io
import json
import os
import random
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse, urlencode
from urllib.request import Request, urlopen

import numpy as np
import requests
import torch
from PIL import Image

import folder_paths

try:
    from comfy_api.latest._input_impl.video_types import VideoFromFile
except ImportError:
    try:
        from comfy_api.input_impl.video_types import VideoFromFile
    except ImportError:
        VideoFromFile = None

_placeholder_video_path: str | None = None


def _get_placeholder_video() -> str:
    """创建并返回占位用最小视频文件路径，供无视频输出时使用。"""
    global _placeholder_video_path
    if _placeholder_video_path and os.path.isfile(_placeholder_video_path):
        return _placeholder_video_path
    try:
        import av
        temp_dir = folder_paths.get_temp_directory()
        for fmt, ext, codec in [("webm", "webm", "libvpx-vp9"), ("mp4", "mp4", "mpeg4")]:
            path = os.path.join(temp_dir, f"_remote_call_placeholder.{ext}")
            try:
                with av.open(path, "w", format=fmt) as out:
                    stream = out.add_stream(codec, rate=1)
                    stream.width = 2
                    stream.height = 2
                    stream.pix_fmt = "yuv420p"
                    frame = av.VideoFrame(2, 2, format="yuv420p")
                    for _ in range(2):
                        for packet in stream.encode(frame):
                            out.mux(packet)
                    out.mux(stream.encode())
                _placeholder_video_path = path
                return path
            except Exception:
                continue
    except Exception:
        pass
    return ""


def normalize_base_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    cleaned = parsed._replace(params="", query="", fragment="")
    url = urlunparse(cleaned)
    return url.rstrip("/")


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _probe_comfyui(base_url: str, timeout_s: float = 3.0) -> dict:
    start = time.monotonic()
    target = f"{base_url.rstrip('/')}/system_stats"
    try:
        req = Request(target, headers={"User-Agent": "ComfyUI-RemoteCall"})
        with urlopen(req, timeout=timeout_s) as resp:
            status = resp.getcode()
            if status and 200 <= status < 300:
                return {
                    "ok": True,
                    "status": status,
                    "elapsed_ms": _elapsed_ms(start),
                    "message": "连接成功",
                }
            return {
                "ok": False,
                "status": status,
                "elapsed_ms": _elapsed_ms(start),
                "message": f"HTTP {status}",
            }
    except HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": _elapsed_ms(start),
            "message": f"HTTP {exc.code}",
        }
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError):
            message = "连接超时"
        elif reason:
            message = f"无法连接: {reason}"
        else:
            message = "无法连接"
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": _elapsed_ms(start),
            "message": message,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": _elapsed_ms(start),
            "message": f"无法连接: {exc}",
        }


def _image_tensor_to_bytes(tensor: torch.Tensor, fmt: str = "PNG") -> bytes:
    """IMAGE 张量转 PNG 字节。"""
    if tensor is None or not isinstance(tensor, torch.Tensor):
        return b""
    arr = tensor.cpu().numpy()
    if arr.ndim == 4:
        arr = arr[0]
    arr = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
    if arr.shape[-1] == 4:
        pil = Image.fromarray(arr, "RGBA")
    else:
        pil = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    pil.save(buf, format=fmt, compress_level=4)
    return buf.getvalue()


def _mask_tensor_to_bytes(tensor: torch.Tensor) -> bytes:
    """MASK 张量转 PNG 字节（灰度）。"""
    if tensor is None or not isinstance(tensor, torch.Tensor):
        return b""
    arr = tensor.cpu().numpy()
    while arr.ndim > 2:
        arr = arr[0]
    arr = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
    pil = Image.fromarray(arr, "L")
    buf = io.BytesIO()
    pil.save(buf, format="PNG", compress_level=4)
    return buf.getvalue()


# 查询结果缓存：prompt_id -> 输出文件列表，供 API 接口返回
_query_outputs_cache: dict[str, list] = {}

# 空产物占位
_EMPTY_IMG = torch.zeros(1, 64, 64, 3)
_EMPTY_AUDIO = {"waveform": torch.zeros(1, 1, 44100), "sample_rate": 44100}


def _download_from_comfy_to_output(base_url: str, filename: str, subfolder: str = "", folder_type: str = "output", timeout: float = 30.0) -> dict | None:
    """从目标 ComfyUI 的 /view 接口下载文件并保存到本地 output/remote_call，返回 {filename, subfolder, type, local_path}。"""
    q = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    url = f"{base_url.rstrip('/')}/view?{urlencode(q)}"
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        out_dir = folder_paths.get_output_directory()
        batch_dir = os.path.join(out_dir, "remote_call")
        os.makedirs(batch_dir, exist_ok=True)
        prefix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=6)) + "_"
        local_name = prefix + filename
        local_path = os.path.join(batch_dir, local_name)
        with open(local_path, "wb") as f:
            f.write(r.content)
        return {"filename": local_name, "subfolder": "remote_call", "type": "output", "local_path": local_path}
    except Exception:
        return None


def _load_audio_from_path(filepath: str) -> dict | None:
    """从本地文件加载音频，返回 {waveform, sample_rate} 供 AUDIO 类型。"""
    try:
        import av
        with av.open(filepath) as af:
            if not af.streams.audio:
                return None
            stream = af.streams.audio[0]
            sr = stream.codec_context.sample_rate
            n_channels = stream.channels
            frames = []
            for frame in af.decode(streams=stream.index):
                buf = torch.from_numpy(frame.to_ndarray())
                if buf.shape[0] != n_channels:
                    buf = buf.view(-1, n_channels).t()
                frames.append(buf)
            if not frames:
                return None
            wav = torch.cat(frames, dim=1)
            if wav.dtype == torch.int16:
                wav = wav.float() / (2 ** 15)
            elif wav.dtype == torch.int32:
                wav = wav.float() / (2 ** 31)
            elif not wav.dtype.is_floating_point:
                return None
            return {"waveform": wav.unsqueeze(0), "sample_rate": sr}
    except Exception:
        return None


def _load_image_from_path(filepath: str) -> torch.Tensor | None:
    """从本地文件加载图片并转为 IMAGE 张量 [1,H,W,C]。"""
    try:
        img = Image.open(filepath)
        img = img.convert("RGB")
        arr = np.array(img).astype(np.float32) / 255.0
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
        arr = np.expand_dims(arr, axis=0)
        return torch.from_numpy(arr)
    except Exception:
        return None


def _upload_file_to_comfy(base_url: str, data: bytes, filename: str, upload_type: str = "input", timeout: float = 60.0) -> dict:
    """上传文件到目标 ComfyUI 的 /upload/image 接口。"""
    url = f"{base_url.rstrip('/')}/upload/image"
    files = {"image": (filename, data, "application/octet-stream")}
    form = {"type": upload_type, "overwrite": "true"}
    try:
        r = requests.post(url, files=files, data=form, timeout=timeout)
        if r.status_code == 200:
            j = r.json()
            name = j.get("name", filename)
            subfolder = j.get("subfolder", "")
            path = f"{subfolder}/{name}" if subfolder else name
            return {"ok": True, "path": path, "name": name, "subfolder": subfolder}
        return {"ok": False, "error": f"HTTP {r.status_code}", "path": None}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": None}


# 工作流节点 class_type -> 上传类型的 input key 映射（仅用于单值注入，dict 注入时直接合并 inputs）
_NODE_INPUT_KEY_MAP = {
    "LoadImage": "image",
    "LoadImageMask": "image",
    "LoadImageBatch": "image",
    "LoadVideo": "file",
    "LoadAudio": "audio",
    "UpscaleModelLoader": "model_name",
}


def _parse_param_overrides(text: str) -> dict:
    """解析参数覆盖：节点ID.字段=值，每行一个或分号分隔。如 16.width=512 或 16.width=512; 16.height=512"""
    out = {}
    if not (text or "").strip():
        return out
    for line in text.replace(";", "\n").split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        left, _, v = line.partition("=")
        left, v = left.strip(), v.strip()
        if "." not in left:
            continue
        nid, _, key = left.partition(".")
        nid, key = nid.strip(), key.strip()
        if not nid or not key:
            continue
        try:
            if "." in v or "e" in v.lower():
                val = float(v)
            else:
                val = int(v)
        except ValueError:
            val = v
        out.setdefault(nid, {})[key] = val
    return out


def _get_input_key_for_node(class_type: str, spec: dict | None = None) -> str | None:
    """根据节点类型返回需要注入的 input key。"""
    key = _NODE_INPUT_KEY_MAP.get(class_type)
    if key:
        return key
    inp = (spec or {}).get("inputs") or {}
    for k in ("image", "file", "audio"):
        if k in inp:
            return k
    return "image"


class RemoteCall_ComfyUI_Check:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "地址": ("STRING", {"default": "", "multiline": False}),
                "超时_ms": ("INT", {"default": 3000, "min": 500, "max": 20000, "step": 100}),
            }
        }

    RETURN_TYPES = ("BOOLEAN", "INT", "STRING", "INT", "STRING")
    RETURN_NAMES = ("是否可连接", "状态码", "消息", "耗时_ms", "地址")
    FUNCTION = "check"
    CATEGORY = "远程调用"
    DESCRIPTION = "检测 ComfyUI 地址是否可连接（访问 /system_stats）。"

    def check(self, 地址: str, 超时_ms: int):
        raw = (地址 or "").strip()
        base = normalize_base_url(raw)
        if not base:
            return (False, -1, "地址无效", 0, raw)
        result = _probe_comfyui(base, max(0.1, 超时_ms / 1000.0))
        status = result.get("status")
        return (
            bool(result.get("ok")),
            int(status) if status is not None else -1,
            str(result.get("message") or ""),
            int(result.get("elapsed_ms") or 0),
            base,
        )


class RemoteCall_Workflow_Upload:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "工作流_JSON": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "placeholder": "粘贴ComfyUI工作流JSON文本",
                    },
                )
            }
        }

    RETURN_TYPES = ("BOOLEAN", "STRING", "INT", "STRING")
    RETURN_NAMES = ("是否有效", "工作流_JSON", "节点数", "消息")
    FUNCTION = "load"
    CATEGORY = "远程调用"
    DESCRIPTION = "上传 ComfyUI 工作流 JSON，用于远程调用。"

    def load(self, 工作流_JSON: str):
        raw = (工作流_JSON or "").strip()
        if not raw:
            return (False, "", 0, "内容为空")
        try:
            data = json.loads(raw)
        except Exception as exc:
            return (False, "", 0, f"JSON 解析失败: {exc}")

        node_count = 0
        if isinstance(data, dict):
            # 工作流格式：有 nodes 数组
            workflow_nodes = data.get("nodes")
            if isinstance(workflow_nodes, list):
                node_count = len(workflow_nodes)
            else:
                # API 格式：根对象为 {node_id: {class_type, inputs}, ...} 或包在 prompt 里
                prompt = data.get("prompt", data)
                if isinstance(prompt, dict) and prompt:
                    first_val = next(iter(prompt.values()), None)
                    if isinstance(first_val, dict) and "class_type" in first_val:
                        node_count = len(prompt)

        compact = json.dumps(data, ensure_ascii=False)
        return (True, compact, node_count, "OK")


def _get_prompt_dict(data: dict) -> tuple[dict | None, str]:
    """
    从工作流 JSON 中提取 API 格式的 prompt 字典。
    返回 (prompt_dict, error_message)。成功时 error_message 为空。
    """
    if not isinstance(data, dict):
        return None, "根节点不是对象"

    candidates = []
    if "prompt" in data:
        candidates.append(("prompt", data["prompt"]))
    if "workflow" in data:
        w = data["workflow"]
        if isinstance(w, dict) and "prompt" in w:
            candidates.append(("workflow.prompt", w["prompt"]))
        elif isinstance(w, dict):
            candidates.append(("workflow", w))
    if not candidates:
        candidates.append(("root", data))

    for name, prompt in candidates:
        if not isinstance(prompt, dict) or not prompt:
            continue
        for val in prompt.values():
            if not isinstance(val, dict):
                continue
            if "class_type" in val:
                return prompt, ""
            if "type" in val:
                return None, (
                    "检测到 UI 格式（含 'type' 而非 'class_type'）。"
                    "请使用 ComfyUI 菜单「File → Save (API Format)」或「导出 API 格式」重新保存工作流。"
                )

    top_keys = list(data.keys())[:5]
    return None, (
        f"未找到 API 格式的 prompt（需 node_id: {{class_type, inputs}} 结构）。"
        f"顶层键: {top_keys}。"
        f"请使用「File → Save (API Format)」导出工作流。"
    )


def _is_connection(val) -> bool:
    """判断 input 值是否为节点连接 [node_id, slot]。"""
    return isinstance(val, list) and len(val) == 2 and isinstance(val[0], (str, int))


def _parse_workflow_io(prompt: dict) -> tuple[dict, list]:
    """
    解析工作流的输入与输出。
    返回 (可配置输入摘要, 输出节点列表)
    """
    if not prompt:
        return {}, []

    all_ids = set(str(k) for k in prompt.keys())
    referenced = set()

    for nid, spec in prompt.items():
        if not isinstance(spec, dict):
            continue
        inp = spec.get("inputs") or {}
        for v in inp.values():
            if _is_connection(v):
                ref_id = str(v[0])
                referenced.add(ref_id)

    output_ids = list(all_ids - referenced)

    configurable = {}
    for nid, spec in prompt.items():
        if not isinstance(spec, dict):
            continue
        inp = spec.get("inputs") or {}
        direct = {}
        for k, v in inp.items():
            if not _is_connection(v):
                direct[k] = v
        if direct:
            configurable[str(nid)] = {
                "class_type": spec.get("class_type", "?"),
                "inputs": direct,
            }

    outputs = []
    for nid in output_ids:
        spec = prompt.get(nid) or prompt.get(int(nid))
        if isinstance(spec, dict):
            outputs.append({
                "node_id": str(nid),
                "class_type": spec.get("class_type", "?"),
            })

    return configurable, outputs


class RemoteCall_Workflow_Parse:
    """解析工作流 JSON，提取可配置输入与输出节点。直接接入工作流上传节点的 JSON 输出。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "工作流_JSON": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT", "INT", "STRING")
    RETURN_NAMES = ("可配置输入_JSON", "输出节点_JSON", "输入项数", "输出节点数", "工作流_JSON")
    FUNCTION = "parse"
    CATEGORY = "远程调用"
    DESCRIPTION = "解析工作流，提取可配置输入（直接值）与输出节点（叶节点）。仅支持 API 格式。"

    def parse(self, 工作流_JSON):
        raw = (工作流_JSON or "").strip()
        if not raw:
            return ("{}", "[]", 0, 0, "")

        try:
            data = json.loads(raw)
        except Exception as exc:
            return ("{}", "[]", 0, 0, "")

        prompt, err = _get_prompt_dict(data)
        if prompt is None:
            return ("{}", "[]", 0, 0, json.dumps(data, ensure_ascii=False))

        configurable, outputs = _parse_workflow_io(prompt)
        inputs_json = json.dumps(configurable, ensure_ascii=False, indent=2)
        outputs_json = json.dumps(outputs, ensure_ascii=False, indent=2)
        input_count = sum(len(v.get("inputs") or {}) for v in configurable.values())
        output_count = len(outputs)
        workflow_json = json.dumps(data, ensure_ascii=False)

        return (inputs_json, outputs_json, input_count, output_count, workflow_json)


# 串联传输用的自定义类型，用于在多个传输节点间传递合并后的 {节点ID: 数据}
TRANSFER_CHAIN = "REMOTE_CALL_TRANSFER"


class RemoteCall_Transfer:
    """
    串联传输节点：指定目标工作流中某个上传类型接口的节点 ID，
    将待测试数据填入，可串联多个节点，信息合并后用于上传提交。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "节点ID": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "placeholder": "目标工作流中上传类型接口的节点 ID",
                    },
                ),
            },
            "optional": {
                "任意": ("*", {"tooltip": "图片/视频/音频等文件数据"}),
                "串联输入": (TRANSFER_CHAIN, {"forceInput": True}),
            },
        }

    RETURN_TYPES = (TRANSFER_CHAIN,)
    RETURN_NAMES = ("串联输出",)
    FUNCTION = "transfer"
    CATEGORY = "远程调用"
    DESCRIPTION = "串联传输：填节点 ID，连接待测数据（图片/视频/音频等），可串联多个节点。参数覆盖请在上传提交节点填写。"

    @classmethod
    def VALIDATE_INPUTS(cls, input_types):
        return True

    def transfer(self, 节点ID: str, 任意=None, 串联输入=None):
        nid = str(节点ID or "").strip()
        merged = dict(串联输入) if isinstance(串联输入, dict) else {}
        if nid and 任意 is not None:
            merged[nid] = 任意
        return (merged,)


class RemoteCall_Param_Override:
    """
    参数覆盖节点：解析 节点ID.字段=值 格式，合并到串联数据中。
    可串联在传输节点之后、上传提交之前。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "参数覆盖": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "placeholder": "16.width=512\n16.height=512\n10.model_name=xxx\n每行 节点ID.字段=值",
                    },
                ),
                "串联输入": (TRANSFER_CHAIN, {"forceInput": True}),
            },
        }

    RETURN_TYPES = (TRANSFER_CHAIN,)
    RETURN_NAMES = ("串联输出",)
    FUNCTION = "override"
    CATEGORY = "远程调用"
    DESCRIPTION = "解析 节点ID.字段=值，合并到串联数据。串联在传输节点之后、上传提交之前。"

    def override(self, 参数覆盖: str, 串联输入=None):
        merged = dict(串联输入) if isinstance(串联输入, dict) else {}
        overrides = _parse_param_overrides(参数覆盖)
        for nid, kv in overrides.items():
            existing = merged.get(nid)
            if isinstance(existing, dict):
                existing = dict(existing)
                existing.update(kv)
                merged[nid] = existing
            else:
                merged[nid] = dict(kv)
        return (merged,)


class RemoteCall_Upload_Submit:
    """
    上传提交节点：接收串联传输的输出 + 工作流 JSON，
    将图片等上传到目标 ComfyUI，注入工作流后提交执行。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "地址": ("STRING", {"default": "http://127.0.0.1:8188", "forceInput": True}),
                "工作流_JSON": ("STRING", {"default": "", "forceInput": True}),
                "串联输入": (TRANSFER_CHAIN, {"forceInput": True}),
            },
            "optional": {
                "超时_秒": ("INT", {"default": 60, "min": 10, "max": 300, "step": 5}),
            },
        }

    RETURN_TYPES = ("STRING", "BOOLEAN", "STRING")
    RETURN_NAMES = ("prompt_id", "是否成功", "消息")
    FUNCTION = "upload_submit"
    CATEGORY = "远程调用"
    DESCRIPTION = "接收串联输入（可接传输节点、参数覆盖节点），上传并注入工作流后提交。"

    def upload_submit(
        self,
        地址: str,
        工作流_JSON: str,
        串联输入,
        超时_秒: int = 60,
    ):
        base = normalize_base_url(str(地址 or "").strip())
        if not base:
            return ("", False, "地址无效")

        raw = str(工作流_JSON or "").strip()
        if not raw:
            return ("", False, "工作流为空")

        try:
            data = json.loads(raw)
        except Exception as exc:
            return ("", False, f"工作流 JSON 解析失败: {exc}")

        prompt, err = _get_prompt_dict(data)
        if prompt is None:
            return ("", False, err or "工作流非 API 格式或缺少 prompt")

        bundle = 串联输入 if isinstance(串联输入, dict) else {}
        if not bundle:
            return ("", False, "串联输入为空，请连接传输节点")

        timeout = max(10, min(300, 超时_秒 or 60))
        errors = []
        uploaded = {}  # node_id -> filename

        for nid, val in bundle.items():
            nid = str(nid)
            if nid not in prompt:
                errors.append(f"节点 {nid} 不在工作流中")
                continue

            spec = prompt[nid]
            if isinstance(val, dict):
                inputs = spec.get("inputs") or {}
                inputs = dict(inputs)
                for k, v in val.items():
                    inputs[k] = v
                spec["inputs"] = inputs
                continue

            class_type = spec.get("class_type", "")
            input_key = _get_input_key_for_node(class_type, spec)
            if not input_key:
                errors.append(f"节点 {nid} 类型 {class_type} 暂不支持注入")
                continue

            filename = None
            if isinstance(val, torch.Tensor):
                arr = val.cpu().numpy()
                ndim = arr.ndim
                if ndim == 2 or (ndim == 3 and arr.shape[-1] == 1):
                    data_bytes = _mask_tensor_to_bytes(val)
                else:
                    data_bytes = _image_tensor_to_bytes(val)
                if not data_bytes:
                    errors.append(f"节点 {nid}: 张量转换失败")
                    continue
                ext = "png"
                fn = f"remote_{nid}_{abs(hash(str(nid) + str(time.time())) % 100000)}.{ext}"
                r = _upload_file_to_comfy(base, data_bytes, fn, timeout=float(timeout))
                if r["ok"]:
                    filename = r["path"]
                else:
                    errors.append(f"节点 {nid}: 上传失败 {r.get('error', '')}")
            elif isinstance(val, str):
                path = val.strip()
                if path and os.path.isfile(path):
                    try:
                        with open(path, "rb") as f:
                            data_bytes = f.read()
                    except Exception as e:
                        errors.append(f"节点 {nid}: 读取文件失败 {e}")
                        continue
                    fn = os.path.basename(path)
                    r = _upload_file_to_comfy(base, data_bytes, fn, timeout=float(timeout))
                    if r["ok"]:
                        filename = r["path"]
                    else:
                        errors.append(f"节点 {nid}: 上传失败 {r.get('error', '')}")
                elif path and (path.startswith("http://") or path.startswith("https://")):
                    filename = path
                elif path:
                    filename = path
                else:
                    errors.append(f"节点 {nid}: 字符串为空")
            else:
                errors.append(f"节点 {nid}: 不支持的数据类型 {type(val).__name__}")

            if filename:
                uploaded[nid] = filename

        if errors:
            return ("", False, "; ".join(errors))

        for nid, filename in uploaded.items():
            spec = prompt.get(nid)
            if not spec:
                continue
            class_type = spec.get("class_type", "")
            input_key = _get_input_key_for_node(class_type, spec)
            if input_key:
                inputs = spec.get("inputs") or {}
                inputs = dict(inputs)
                inputs[input_key] = filename
                spec["inputs"] = inputs

        try:
            payload = {"prompt": prompt}
            r = requests.post(f"{base}/prompt", json=payload, timeout=timeout)
            if r.status_code in (200, 201):
                j = r.json()
                pid = j.get("prompt_id", "")
                return (pid, True, f"已提交 prompt_id={pid}")
            return ("", False, f"提交失败 HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            return ("", False, f"提交异常: {e}")


class RemoteCall_Query_Result:
    """
    查询执行结果：轮询目标 ComfyUI 的 /history/{prompt_id}，
    等待任务完成后拉取产物（图片、视频、音频等），可串联、可指定节点ID。
    产物可连接到预览图像、合成视频、保存音频等节点。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "地址": ("STRING", {"default": "http://127.0.0.1:8188", "forceInput": True}),
                "prompt_id": ("STRING", {"default": "", "forceInput": True}),
            },
            "optional": {
                "节点ID": (
                    "STRING",
                    {"default": "", "multiline": False, "placeholder": "留空取全部，填则只取该输出节点的产物"},
                ),
                "仅查询一次": ("BOOLEAN", {"default": False}),
                "轮询间隔_秒": ("FLOAT", {"default": 1.0, "min": 0.5, "max": 10.0, "step": 0.5}),
                "超时_秒": ("INT", {"default": 120, "min": 10, "max": 600, "step": 10}),
            },
        }

    RETURN_TYPES = ("IMAGE", "VIDEO", "AUDIO", "BOOLEAN", "STRING")
    RETURN_NAMES = ("产物_图像", "产物_视频", "产物_音频", "是否成功", "消息")
    FUNCTION = "query"
    CATEGORY = "远程调用"
    DESCRIPTION = "查询执行结果，可指定节点ID。产物可接预览图像、合成视频、保存音频节点。"

    @classmethod
    def VALIDATE_INPUTS(cls, input_types):
        return True

    def query(
        self,
        地址: str,
        prompt_id: str,
        节点ID: str = "",
        仅查询一次: bool = False,
        轮询间隔_秒: float = 1.0,
        超时_秒: int = 120,
    ):
        def _empty_video():
            ph = _get_placeholder_video()
            if VideoFromFile and ph:
                try:
                    return VideoFromFile(ph)
                except Exception:
                    return ""
            return ""

        base = normalize_base_url(str(地址 or "").strip())
        if not base:
            return (_EMPTY_IMG, _empty_video(), _EMPTY_AUDIO, False, "地址无效")

        pid = str(prompt_id or "").strip()
        if not pid:
            return (_EMPTY_IMG, _empty_video(), _EMPTY_AUDIO, False, "prompt_id 为空")

        interval = max(0.5, min(10.0, 轮询间隔_秒 or 1.0))
        timeout = max(10, min(600, 超时_秒 or 120))
        url = f"{base}/history/{pid}"
        deadline = time.monotonic() + timeout

        def do_query():
            try:
                r = requests.get(url, timeout=10)
                if r.status_code != 200:
                    return None, f"查询失败 HTTP {r.status_code}"
                return r.json(), None
            except Exception as e:
                return None, f"查询异常: {e}"

        while time.monotonic() < deadline:
            data, err = do_query()
            if err:
                return (_EMPTY_IMG, _empty_video(), _EMPTY_AUDIO, False, err)

            if pid in data:
                item = data[pid]
                status = item.get("status") or {}
                status_str = status.get("status_str", "")
                outputs = item.get("outputs") or {}

                filter_nid = str(节点ID or "").strip()

                def collect_files(key: str) -> list:
                    infos = []
                    for node_id, out in outputs.items():
                        if filter_nid and str(node_id) != filter_nid:
                            continue
                        if not isinstance(out, dict):
                            continue
                        arr = out.get(key)
                        if isinstance(arr, list):
                            for f in arr:
                                if isinstance(f, dict) and "filename" in f:
                                    infos.append({
                                        "node_id": node_id,
                                        "filename": f["filename"],
                                        "subfolder": f.get("subfolder", ""),
                                        "type": f.get("type", "output"),
                                    })
                    return infos

                def download_and_collect(key: str, kind: str) -> list:
                    infos = collect_files(key)
                    local_refs = []
                    for info in infos:
                        ref = _download_from_comfy_to_output(base, info["filename"], info["subfolder"], info["type"])
                        if ref is not None:
                            ref["kind"] = kind
                            ref["node_id"] = info.get("node_id", "")
                            local_refs.append(ref)
                    return local_refs

                image_refs = download_and_collect("images", "image") + download_and_collect("gifs", "gif")
                video_refs = download_and_collect("videos", "video")
                audio_refs = download_and_collect("audio", "audio")
                all_refs = image_refs + video_refs + audio_refs

                _query_outputs_cache[pid] = all_refs

                out_img = _EMPTY_IMG
                if image_refs:
                    tensors = [_load_image_from_path(r["local_path"]) for r in image_refs if r.get("local_path")]
                    tensors = [t for t in tensors if t is not None]
                    if tensors:
                        out_img = torch.cat(tensors, dim=0)

                out_video = None
                if video_refs and video_refs[0].get("local_path"):
                    path = video_refs[0]["local_path"]
                    if VideoFromFile is not None:
                        try:
                            out_video = VideoFromFile(path)
                        except Exception:
                            out_video = path
                    else:
                        out_video = path

                out_audio = _EMPTY_AUDIO
                if audio_refs and audio_refs[0].get("local_path"):
                    loaded = _load_audio_from_path(audio_refs[0]["local_path"])
                    if loaded is not None:
                        out_audio = loaded

                ok = status_str == "success"
                ni, nv, na = len(image_refs), len(video_refs), len(audio_refs)
                total = ni + nv + na
                msg = f"status={status_str}, 共 {total} 个输出(图{ni} 视{nv} 音{na})" if ok else f"执行失败: {status_str}"

                if out_video is None and VideoFromFile is not None:
                    ph = _get_placeholder_video()
                    if ph:
                        try:
                            out_video = VideoFromFile(ph)
                        except Exception:
                            out_video = ph
                    else:
                        out_video = ""
                elif out_video is None:
                    out_video = ""
                return (out_img, out_video, out_audio, ok, msg)

            if 仅查询一次:
                return (_EMPTY_IMG, _empty_video(), _EMPTY_AUDIO, False, "任务未完成（仅查询一次）")

            time.sleep(interval)

        return (_EMPTY_IMG, _empty_video(), _EMPTY_AUDIO, False, f"超时 {timeout} 秒，任务可能仍在队列或执行中")
