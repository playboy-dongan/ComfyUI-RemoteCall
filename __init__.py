from __future__ import annotations

from . import server  # 注册 API 路由 /remote_call/outputs
from .nodes import (
    RemoteCall_ComfyUI_Check,
    RemoteCall_Workflow_Upload,
    RemoteCall_Workflow_Parse,
    RemoteCall_Transfer,
    RemoteCall_Param_Override,
    RemoteCall_Upload_Submit,
    RemoteCall_Query_Result,
)

NODE_CLASS_MAPPINGS = {
    "RemoteCall_ComfyUI_Check": RemoteCall_ComfyUI_Check,
    "RemoteCall_Workflow_Upload": RemoteCall_Workflow_Upload,
    "RemoteCall_Workflow_Parse": RemoteCall_Workflow_Parse,
    "RemoteCall_Transfer": RemoteCall_Transfer,
    "RemoteCall_Param_Override": RemoteCall_Param_Override,
    "RemoteCall_Upload_Submit": RemoteCall_Upload_Submit,
    "RemoteCall_Query_Result": RemoteCall_Query_Result,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RemoteCall_ComfyUI_Check": "ComfyUI 连接检测",
    "RemoteCall_Workflow_Upload": "工作流上传",
    "RemoteCall_Workflow_Parse": "工作流解析",
    "RemoteCall_Transfer": "传输节点",
    "RemoteCall_Param_Override": "参数覆盖",
    "RemoteCall_Upload_Submit": "上传提交",
    "RemoteCall_Query_Result": "查询执行结果",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
