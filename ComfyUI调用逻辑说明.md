# ComfyUI 远程调用工具 - API 调用逻辑说明

## 一、ComfyUI 核心 API

### 1. 连接检测
- **接口**: `GET {base_url}/system_stats`
- **用途**: 检测目标 ComfyUI 是否在线

### 2. 文件上传
- **接口**: `POST {base_url}/upload/image`
- **请求**: multipart/form-data
  - `image`: 文件内容（字段名固定为 `image`，但可上传任意类型）
  - `type`: `"input"` | `"temp"` | `"output"`，默认 input
  - `subfolder`: 子目录，如 `"references"`
  - `overwrite`: `"true"` 可覆盖同名文件
- **响应**: `{"name": "filename.png", "subfolder": "", "type": "input"}`
- **说明**: 尽管路径叫 `/upload/image`，实际可上传图片、视频、音频等，文件会保存到 `input` 目录

### 3. 蒙版上传（特殊）
- **接口**: `POST {base_url}/upload/mask`
- **额外参数**: `original_ref` - 需引用原图，用于 alpha 合成
- **用途**: 与 LoadImageMask 配合，一般远程调用较少用

### 4. 执行工作流
- **接口**: `POST {base_url}/prompt`
- **请求体**: `{"prompt": workflow_json}`（API 格式）
- **响应**: 含 `prompt_id`，用于后续查询

### 5. 查询执行结果
- **接口**: `GET {base_url}/history/{prompt_id}`
- **响应**: 执行状态、输出节点结果、图片路径等

---

## 二、工作流 JSON 中的文件引用

### API 格式结构
```json
{
  "3": {
    "class_type": "LoadImage",
    "inputs": {
      "image": "my_image.png"
    }
  },
  "7": {
    "class_type": "LoadVideo",
    "inputs": {
      "file": "video.mp4"
    }
  },
  "9": {
    "class_type": "LoadAudio",
    "inputs": {
      "audio": "audio.mp3"
    }
  }
}
```

### 各节点输入键
| 节点类型   | 输入键   | 说明                     |
|------------|----------|--------------------------|
| LoadImage  | `image`  | 图片文件名               |
| LoadImageMask | `image` | 蒙版文件名               |
| LoadVideo  | `file`   | 视频文件名               |
| LoadAudio  | `audio`  | 音频文件名               |

### 路径规则
- **默认**: 文件名直接放在 `input` 目录下，如 `"image": "test.png"`
- **子目录**: 部分节点支持 `directory`，如 `"image": "ref.png", "directory": "references"`
- **带标注**: `"filename [input]"` 显式指定 input 目录（API 格式较少用）

---

## 三、远程调用完整流程

```
1. 连接检测 → 确认目标 ComfyUI 可访问
2. 工作流上传 → 解析/校验工作流 JSON
3. 工作流解析 → 提取可配置输入（LoadImage 的 image、LoadVideo 的 file 等）
4. 统一上传 → 将本地图片/视频等上传到目标 input 目录
5. 工作流注入 → 用上传结果中的文件名，替换工作流中对应节点的 inputs
6. 执行工作流 → POST /prompt
7. 轮询结果   → GET /history/{prompt_id}
```

---

## 四、传输节点（串联）

### 功能
- **节点ID**: 目标工作流中某个上传类型接口（如 LoadImage）的节点 ID
- **任意**: 待测试的数据（图片、蒙版、文本等），可连接任意类型
- **串联输入**: 可选，连接上一个传输节点的「串联输出」，形成链条
- **串联输出**: 合并后的 `{节点ID: 数据}` 字典，可接下一个传输节点或提交节点

### 串联用法
1. 传输节点 A：节点ID=3，任意=图片1，串联输入=空 → 串联输出={3: 图片1}
2. 传输节点 B：节点ID=7，任意=图片2，串联输入=A 的串联输出 → 串联输出={3: 图片1, 7: 图片2}
3. 串联输出接入「上传提交」节点

### 五、上传提交节点

- **输入**: 地址、工作流 JSON（API 格式）、串联输入（来自传输节点）
- **流程**: 上传图片/文件到目标 ComfyUI → 注入工作流 → POST /prompt 提交
- **输出**: prompt_id、是否成功、消息、已注入工作流 JSON
- **支持**: LoadImage、LoadImageMask、LoadVideo、LoadAudio 等；IMAGE/MASK 张量、本地文件路径、URL

---

## 五、参考

- [Replicate ComfyUI API](https://replicate.com/comfyui/any-comfyui-workflow/readme) - 工作流 JSON 与 URL/上传说明
- ComfyUI `server.py` - `/upload/image`、`/prompt`、`/history` 实现
- `folder_paths.py` - `annotated_filepath`、`get_input_directory`
