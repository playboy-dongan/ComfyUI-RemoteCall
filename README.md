# ComfyUI 远程调用工具

ComfyUI 自定义节点扩展，用于**远程调用**另一台 ComfyUI 实例执行工作流，支持上传资源、注入参数、轮询结果并拉取产物（图片、视频、音频）。

## 功能特性

- **连接检测**：检测目标 ComfyUI 是否在线
- **工作流上传/解析**：解析 API 格式工作流，提取可配置输入与输出节点
- **传输节点**：串联多个节点，收集 `{节点ID: 文件数据}` 供提交
- **参数覆盖**：通过 `节点ID.字段=值` 格式覆盖工作流参数
- **上传提交**：上传资源到目标 ComfyUI，注入工作流后 POST 执行
- **查询执行结果**：轮询目标实例，拉取产物（IMAGE、VIDEO、AUDIO）供后续节点使用
- **API 接口**：`GET /remote_call/outputs?prompt_id=xxx` 获取输出文件列表

## 安装

### 方式一：ComfyUI Manager（推荐）

1. 打开 ComfyUI Manager
2. 点击 "Install Custom Nodes"
3. 搜索 `ComfyUI-RemoteCall` 或 `远程调用`
4. 安装

### 方式二：手动安装

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/YOUR_USERNAME/ComfyUI-RemoteCall.git
```

重启 ComfyUI。

## 依赖

- ComfyUI（含 comfy_extras，用于 VIDEO/AUDIO 类型）
- `requests`、`av`（通常已随 ComfyUI 安装）

若缺少依赖，可执行：

```bash
pip install requests av
```

## 节点列表

| 节点 | 说明 |
|------|------|
| ComfyUI 连接检测 | 检测目标地址是否可连接 |
| 工作流上传 | 粘贴工作流 JSON，校验并透传 |
| 工作流解析 | 解析 API 格式，提取可配置输入与输出节点 |
| 传输节点 | 节点ID + 任意数据 → 串联输出 |
| 参数覆盖 | 解析 `节点ID.字段=值`，合并到串联数据 |
| 上传提交 | 串联输入 + 地址 + 工作流 → 提交执行 |
| 查询执行结果 | 地址 + prompt_id → 产物（图像/视频/音频） |

## 基本流程

```
[工作流解析] ──工作流_JSON──→ [上传提交]
[连接检测] ──地址──→ [上传提交]
[传输] → [参数覆盖] ──串联输出──→ [上传提交]

[上传提交] ──prompt_id──→ [查询执行结果]
[连接检测] ──地址──→ [查询执行结果]
[查询执行结果] ──产物_图像/视频/音频──→ [预览/保存节点]
```

## 参数覆盖格式

在**参数覆盖**节点中填写，每行一条：

```
16.width=512
16.height=512
10.model_name=RealESRGAN_x4plus.pth
```

或分号分隔：`16.width=512; 16.height=512`

## 重要说明：同实例死锁

若远程调用工作流与目标 ComfyUI **在同一实例**运行，会形成死锁（任务排队等当前工作流结束，当前工作流又在等任务完成）。

**正确用法**：远程调用工作流在 ComfyUI-A 运行，目标地址填 ComfyUI-B（另一实例）。

详见 [流程说明.md](流程说明.md)。

## API 接口

- `GET /remote_call/outputs?prompt_id=xxx`：获取查询结果中的输出文件列表（含 view_url）

## 文件结构

```
ComfyUI-RemoteCall/
├── __init__.py
├── nodes.py
├── server.py
├── README.md
├── 流程说明.md
└── ComfyUI调用逻辑说明.md
```

## 发布

- **手动上传 GitHub + 同步 ComfyUI 社区**：详见 [发布指南.md](发布指南.md)

## License

MIT
