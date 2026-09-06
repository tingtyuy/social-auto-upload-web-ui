"""
Zr.Admin.NET ComfyUI 任务 HTTP 客户端 — 无认证（三端点已加 [AllowAnonymous]）。

拉取已完成任务的输出图片（网络 URL -> 本地临时文件）+ 工作流变量，供定时图集发布使用。

settings 中 `zr_base_url` 为 ZR 服务根地址，例如 http://192.168.1.10:5000
（规则可各自覆盖，优先使用规则上的 zr_base_url）。
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from util._logger import get_channel_logger  # noqa: E402

logger = get_channel_logger("zr_task_source")

_TIMEOUT = 30


def _base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _unwrap(resp_json: dict, action: str):
    """Zr 响应统一 {code, msg, data}；code==200 时返回 data，否则抛错。"""
    code = resp_json.get("code")
    if code != 200:
        raise RuntimeError(f"{action} 失败: code={code}, msg={resp_json.get('msg')}")
    return resp_json.get("data")


def _get(base_url: str, path: str, params: dict | None = None) -> dict:
    url = f"{_base(base_url)}{path}"
    resp = requests.get(url, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_task_list(base_url: str, status: str = "success", func_type: str = "",
                  prompt: str = "", page_num: int = 1, page_size: int = 50) -> list:
    """返回 ComfyuiTaskView 列表（camelCase 字段）。"""
    params: dict = {"pageNum": page_num, "pageSize": page_size}
    if status:
        params["status"] = status
    if func_type:
        params["funcType"] = func_type
    if prompt:
        params["prompt"] = prompt
    data = _unwrap(_get(base_url, "/comfyui/task/list", params), "获取任务列表")
    return (data or {}).get("result") or []


def get_task_detail(base_url: str, task_id) -> dict:
    """返回 ComfyuiTask 实体（camelCase），含 variableValues / workflowId 等。"""
    data = _unwrap(_get(base_url, f"/comfyui/task/detail/{task_id}"), f"获取任务详情 {task_id}")
    return data or {}


def get_workflow_variables(base_url: str, workflow_id) -> dict:
    """返回 {nodeId: label} 映射，用于占位符解析。"""
    data = _unwrap(_get(base_url, f"/comfyui/workflow/variables/{workflow_id}"),
                   f"获取工作流变量 {workflow_id}")
    mapping = {}
    for node in (data or []):
        node_id = node.get("nodeId")
        label = node.get("label")
        if node_id is not None and label:
            mapping[str(node_id)] = str(label)
    return mapping


def update_publish_status(base_url: str, task_id, publish_status: str) -> bool:
    """publish_status: 'pending' | 'published'（ZR 端仅接受这两个小写值）。"""
    url = f"{_base(base_url)}/comfyui/task/publish-status/{task_id}"
    resp = requests.post(url, json={"publishStatus": publish_status}, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = _unwrap(resp.json(), f"更新发布状态 {task_id}")
    return bool(data)


def parse_output_urls(output_urls_raw) -> list:
    """OutputUrls 为 JSON 字符串数组，解析为 list[dict]。"""
    if not output_urls_raw:
        return []
    if isinstance(output_urls_raw, list):
        return output_urls_raw
    try:
        parsed = json.loads(output_urls_raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def image_items(output_urls_raw, limit: int = 0) -> list:
    """从 OutputUrls 中筛选 type=='image' 的项，可选数量上限（0=全部）。"""
    items = parse_output_urls(output_urls_raw)
    images = [it for it in items if str(it.get("type", "")).lower() == "image"]
    if limit and limit > 0:
        images = images[:limit]
    return images


def download_images(base_url: str, output_urls_raw, dest_dir: Path, limit: int = 0) -> list:
    """下载 type=='image' 的输出到 dest_dir，返回本地路径列表。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    items = image_items(output_urls_raw, limit)
    local_paths = []
    for idx, it in enumerate(items):
        url = it.get("url")
        if not url:
            continue
        filename = it.get("filename") or f"img_{idx}_{uuid.uuid4().hex[:8]}.png"
        # 仅保留文件名部分，避免路径穿越
        filename = Path(filename).name or f"img_{idx}.png"
        local_path = dest_dir / filename
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
            local_paths.append(str(local_path))
            logger.info(f"[zr] 已下载图片 {url} -> {local_path}")
        except Exception as e:
            logger.error(f"[zr] 下载图片失败 {url}: {e}")
    return local_paths


def _to_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def resolve_placeholders(template: str, variable_values: dict, labels: dict) -> str:
    """将模板中 {{label}} 替换为对应变量值。

    labels: {nodeId: label}。占位符 key 优先取变量的 Label，同时允许直接用 nodeId。
    未匹配的占位符原样保留（便于用户发现配置问题）。
    """
    if not template:
        return ""
    variable_values = variable_values or {}
    repl = {}
    for node_id, label in (labels or {}).items():
        if node_id in variable_values:
            repl[str(label)] = _to_str(variable_values[node_id])
    for node_id, val in variable_values.items():
        repl.setdefault(str(node_id), _to_str(val))

    def _sub(m):
        key = m.group(1).strip()
        return repl.get(key, m.group(0))

    return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", _sub, template)
