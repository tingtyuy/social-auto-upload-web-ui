"""
定时图集发布 Blueprint
======================
用户定义「定时发布规则」，后台调度线程按间隔从 Zr.Admin.NET ComfyUI 任务
拉取已完成任务的输出图片（网络 URL -> 本地临时文件），解析工作流变量生成
标题/描述，逐账号发布到社交平台，并回写 ZR 的 publishStatus。

- 规则表:  scheduled_publish_rules
- 运行表:  scheduled_publish_runs
- 明细表:  scheduled_publish_items
"""

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

try:
    from croniter import CroniterBadCronError, croniter as _croniter
    _CRONITER_AVAILABLE = True
except ImportError:  # 依赖缺失时降级为间隔调度，不阻断服务启动
    _CRONITER_AVAILABLE = False
    _croniter = None
    CroniterBadCronError = ValueError

from flask import Blueprint, jsonify, request

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from conf import BASE_DIR  # noqa: E402
from util._logger import get_channel_logger  # noqa: E402

logger = get_channel_logger("scheduled_publish")

scheduled_publish_bp = Blueprint('scheduled_publish', __name__, url_prefix='/api/scheduled-publish')

DB_PATH = BASE_DIR / "db" / "database.db"
DOWNLOAD_ROOT = BASE_DIR / "uploads" / "scheduled"


# ── DB helpers ───────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _rule_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["accounts"] = json.loads(d.get("accounts") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["accounts"] = []
    try:
        d["extra_kwargs"] = json.loads(d.get("extra_kwargs") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["extra_kwargs"] = {}
    _tags = d.get("tags")
    if isinstance(_tags, list):
        d["tags"] = [str(t).strip() for t in _tags if str(t).strip()]
    elif isinstance(_tags, str):
        _s = _tags.strip()
        if not _s:
            d["tags"] = []
        else:
            try:
                _parsed = json.loads(_s)
                d["tags"] = _parsed if isinstance(_parsed, list) else [str(_parsed)]
            except (json.JSONDecodeError, TypeError):
                d["tags"] = [x.strip() for x in _s.split(",") if x.strip()]
    else:
        d["tags"] = []
    d["enabled"] = bool(d.get("enabled"))
    d["notify_on"] = d.get("notify_on") or "fail"
    return d


def _get_accounts(account_ids) -> list:
    """按 id 列表从 user_info 取账号，返回 [{id, platform_id, cookie_path, name}]。"""
    if not account_ids:
        return []
    accounts = []
    with _get_db() as conn:
        for aid in account_ids:
            row = conn.execute(
                "SELECT id, type AS platform_id, filePath AS cookie_path, userName AS name "
                "FROM user_info WHERE id = ?", (aid,)
            ).fetchone()
            if row:
                accounts.append({
                    "id": row["id"],
                    "platform_id": row["platform_id"],
                    "cookie_path": row["cookie_path"],
                    "name": row["name"],
                })
    return accounts


def _get_accounts_by_type(account_type: int) -> list:
    """按账号类型取全部账号：0=正常号，1=养号。"""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, type AS platform_id, filePath AS cookie_path, userName AS name "
            "FROM user_info WHERE account_type=? ORDER BY id",
            (account_type,),
        ).fetchall()
    return [{
        "id": row["id"],
        "platform_id": row["platform_id"],
        "cookie_path": row["cookie_path"],
        "name": row["name"],
    } for row in rows]


def _resolve_accounts(rule: dict) -> list:
    """解析规则目标账号。

    account_scope:
      - all_normal （默认）: 全部正常号
      - all_raising        : 全部养号
      - selected           : 按 accounts（id 列表）
    兼容旧数据：scope 为空时，选过账号按账号，否则按全部正常号。
    """
    scope = str(rule.get("account_scope") or "").strip().lower()
    selected = rule.get("accounts") or []
    if scope == "all_raising":
        return _get_accounts_by_type(1)
    if scope == "selected":
        return _get_accounts(selected)
    if selected:
        return _get_accounts(selected)
    return _get_accounts_by_type(0)


# ── 核心发布逻辑 ────────────────────────────────────────────

def _resolve_base_url(rule: dict) -> str:
    base = rule.get("zr_base_url") or ""
    if base:
        return base
    try:
        from impl.settings import read_settings
        return read_settings().get("zr_base_url") or ""
    except Exception:
        return ""


def _create_run(run_id, rule_id, task_id, task_name, total_images, topic=""):
    now = datetime.now().isoformat()
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO scheduled_publish_runs
               (id, rule_id, task_id, task_name, status, total_images,
                success_count, failed_count, started_at, error_message, topic)
               VALUES (?, ?, ?, ?, 'running', ?, 0, 0, ?, '', ?)""",
            (run_id, rule_id, str(task_id), task_name or "", total_images, now, topic or ""),
        )
    return run_id


def _finish_run(run_id, status, success_count, failed_count, error_message=""):
    with _get_db() as conn:
        conn.execute(
            """UPDATE scheduled_publish_runs
               SET status=?, success_count=?, failed_count=?,
                   finished_at=?, error_message=? WHERE id=?""",
            (status, success_count, failed_count,
             datetime.now().isoformat(), error_message or "", run_id),
        )


def _record_item(run_id, account, status, error_message=""):
    item_id = str(uuid.uuid4())
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO scheduled_publish_items
               (id, run_id, account_id, account_name, platform_id, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (item_id, run_id, account["id"], account["name"] or "",
             account["platform_id"], status, error_message or ""),
        )


def _do_publish_one(account, local_paths, title, desc, tags, extra_kwargs) -> bool:
    """对单个账号调用平台 publish_image。返回是否成功。"""
    from impl.registry import get_platform
    platform_obj = get_platform(account["platform_id"])
    if not platform_obj:
        raise ValueError(f"未找到平台实例 platform_id={account['platform_id']}")

    kwargs = dict(
        title=title or "",
        files=local_paths,
        tags=tags or [],
        account_file=[account["cookie_path"]],
        desc=desc or "",
        cover_path="",
        dry_run=False,
    )
    if isinstance(extra_kwargs, dict):
        for k, v in extra_kwargs.items():
            if k not in ("title", "files", "tags", "account_file", "desc"):
                kwargs[k] = v

    fn = platform_obj.publish_image
    if asyncio.iscoroutinefunction(fn):
        result = asyncio.run(fn(**kwargs))
    else:
        result = fn(**kwargs)
    return bool(result)


def _maybe_notify(rule: dict, run_id: str, status: str, summary: str):
    """按规则 notify_on 决定是否发邮件。fail=仅失败, always=总是, never=不发。"""
    notify_on = rule.get("notify_on") or "fail"
    if notify_on == "never":
        return
    if notify_on == "fail" and status in ("success", "partial"):
        return
    try:
        from util.mail import send_mail
        name = rule.get("name") or rule.get("id")
        subject = f"[定时图集发布] {status} - 规则 {name}"
        body = (
            f"规则: {name}\n"
            f"运行: {run_id}\n"
            f"状态: {status}\n"
            f"详情: {summary}\n"
        )
        send_mail(subject, body)
    except Exception as e:
        logger.error(f"[scheduled] 邮件通知异常: {e}", exc_info=True)


def _build_text(rule: dict, base_url: str, task: dict):
    """用指定任务的变量解析标题/描述/标签/extra_kwargs（所有账号共用）。"""
    from services import zr_task_source as zr

    task_id = str(task.get("id"))
    variable_values: dict = {}
    workflow_id = str(rule.get("workflow_id") or "")
    try:
        detail = zr.get_task_detail(base_url, task_id)
        if detail:
            vv = detail.get("variableValues")
            if isinstance(vv, str) and vv:
                try:
                    variable_values = json.loads(vv)
                except (json.JSONDecodeError, TypeError):
                    variable_values = {}
            elif isinstance(vv, dict):
                variable_values = vv
            if not workflow_id:
                workflow_id = str(detail.get("workflowId") or "")
    except Exception as e:
        logger.warning(f"[scheduled] 任务 {task_id} 详情获取失败: {e}")
    if not workflow_id:
        workflow_id = str(task.get("workflowId") or "")

    labels: dict = {}
    if workflow_id:
        try:
            labels = zr.get_workflow_variables(base_url, workflow_id)
        except Exception as e:
            logger.warning(f"[scheduled] 工作流变量获取失败 {workflow_id}: {e}")

    # 任务标题 = ZR 任务的 Title 字段（默认 = 正向提示词全文，即白话画面描述）
    # 任务描述 = ZR 任务的 Description 字段（AI 工厂写入的原文，如文言/引文原句）
    task_title = ""
    task_desc = ""
    if detail:
        task_title = str(detail.get("title") or "").strip()
        task_desc = str(detail.get("description") or "").strip()
    if not task_title:
        task_title = _prompt_text(variable_values, labels)
    if not task_desc:
        # 描述缺省回退：白话提示词全文
        task_desc = task_title

    title_tpl = rule.get("title_template") or ""
    if str(title_tpl).strip():
        title = zr.resolve_placeholders(title_tpl, variable_values, labels)
    else:
        # 默认：发布标题 = 任务描述（原文）的前 15 个字
        title = task_desc[:15]

    desc_tpl = rule.get("desc_template") or ""
    if str(desc_tpl).strip():
        desc = zr.resolve_placeholders(desc_tpl, variable_values, labels)
    else:
        # 默认：发布描述 = 任务描述（原文）全文
        desc = task_desc
    tags_raw = rule.get("tags") or ""
    if isinstance(tags_raw, str):
        tags = [x.strip() for x in tags_raw.split(",") if x.strip()]
    else:
        tags = list(tags_raw)
    extra_kwargs = rule.get("extra_kwargs") or {}
    return title, desc, tags, extra_kwargs


def _prompt_text(variable_values: dict, labels: dict) -> str:
    """取任务可变参数中正向提示词节点的文本（全文）。

    正向提示词节点 = 工作流变量里 label 含「提示词/prompt/正向/positive」的节点，
    且排除负向（「负向/negative」）；找不到时退而取第一个变量值。空则返回空串。
    """
    prompt_node_id = None
    for nid, label in (labels or {}).items():
        lv = str(label or "").lower()
        if any(k in lv for k in ("负向", "negative")):
            continue
        if any(k in lv for k in ("提示词", "prompt", "正向", "positive")):
            prompt_node_id = nid
            break
    if prompt_node_id is None and variable_values:
        # 退而取第一个变量值；排除明确登记为负向的节点
        for nid, _ in (labels or {}).items():
            lv = str(labels.get(nid) or "").lower()
            if any(k in lv for k in ("负向", "negative")):
                continue
            if nid in variable_values:
                prompt_node_id = nid
                break
        if prompt_node_id is None:
            prompt_node_id = next(iter(variable_values))
    if prompt_node_id is None:
        return ""
    return " ".join(str(variable_values.get(prompt_node_id) or "").split())


def _batch_image_limit(rule: dict, task_count: int, account_count: int) -> int:
    """每个任务最多取图数量。

    - 单任务批次（默认每批 1 个任务）：图片数量上限，0 = 取该任务全部图片。
    - 多任务批次：取 max(1, 账号数) 张供「第 j 个账号取每个任务第 j 张」分层，
      受图片数量上限约束（不足则靠后的账号无图跳过）。
    """
    try:
        image_count = int(rule.get("image_count") or 0)
    except (TypeError, ValueError):
        image_count = 0
    if task_count <= 1:
        return image_count
    per_task = max(1, account_count)
    if image_count >= 1:
        per_task = min(per_task, image_count)
    return per_task


def _process_batch(rule: dict, base_url: str, tasks: list, accounts: list | None = None):
    """处理一组有序任务。

    - 单个任务（默认每批 1 个任务）：取该任务全部图片发布。
      accounts 显式传入（顺序配对模式只传当前轮到的账号）或为全部账号。
    - 多个任务（分层合并模式）：第 i 个账号 = 按任务顺序，取每个任务的第 i 张拼成图集。
    - 每个任务一个子目录下载，避免跨任务文件名冲突。
    - 多任务时某任务图片不足第 i 张，该账号图集跳过该任务（不报错）。
    - 标题/描述用第一个任务的变量解析，所有账号共用。
    - 有任一账号成功即把全部任务回写 publishStatus=published。
    """
    from services import zr_task_source as zr
    from services.risk_control import risk_control

    if not tasks:
        return
    if accounts is None:
        accounts = _resolve_accounts(rule)
    if not accounts:
        logger.warning(f"[scheduled] 规则 {rule.get('id')} 无有效账号，跳过")
        return

    run_id = str(uuid.uuid4())
    dest_dir = DOWNLOAD_ROOT / run_id
    per_task = _batch_image_limit(rule, len(tasks), len(accounts))

    task_images: list = []
    task_meta: list = []
    for t in tasks:
        t_id = str(t.get("id"))
        sub = dest_dir / t_id
        local_paths = zr.download_images(base_url, t.get("outputUrls"), sub, per_task)
        task_images.append(local_paths)
        task_meta.append({"id": t_id, "name": t.get("taskName") or ""})
        logger.info(f"[scheduled] 规则 {rule.get('id')} 任务 {t_id} 下载 {len(local_paths)} 张")

    title, desc, tags, extra_kwargs = _build_text(rule, base_url, tasks[0])

    task_id_repr = ",".join(m["id"] for m in task_meta)
    name_parts = [m["name"] for m in task_meta if m["name"]]
    task_name_repr = " + ".join(name_parts) if name_parts else task_id_repr
    total_images = sum(len(x) for x in task_images)
    _create_run(run_id, rule.get("id"), task_id_repr, task_name_repr, total_images,
                rule.get("zr_topic") or "")

    success_count = 0
    failed_count = 0
    single_task = len(tasks) == 1
    for idx, account in enumerate(accounts):
        if single_task:
            # 默认模式：一批 = 1 个任务的全部图片，各账号发布相同内容
            album = task_images[0] if task_images else []
            if not album:
                failed_count += 1
                _record_item(run_id, account, "failed", "无可用图片（任务没有图片输出）")
                continue
        else:
            # 分层模式：第 idx 个账号取每个任务的第 idx 张图；
            # 任一任务缺该层图片 → 该账号跳过（不拼凑不完整图集）
            if any(idx >= len(imgs) for imgs in task_images):
                failed_count += 1
                _record_item(run_id, account, "failed", f"图片不足（第 {idx + 1} 层无图）")
                continue
            album = [imgs[idx] for imgs in task_images]
        # 风控：发布前检查（同账号/全局间隔自动等待 + 随机抖动 + 每日限额 + 失败熔断）
        try:
            allowed, waited, reason = risk_control.before_publish(account["id"])
            if not allowed:
                logger.warning(f"[scheduled] 风控拦截 账号 {account['id']}: {reason}")
                _record_item(run_id, account, "skipped", reason or "风控拦截")
                continue
            if waited > 0:
                logger.info(f"[scheduled] 风控等待 {waited:.0f}s（账号 {account['id']} 发布间隔）")
        except Exception as e:
            logger.warning(f"[scheduled] 风控检查失败（降级放行）: {e}")
        try:
            ok = _do_publish_one(account, album, title, desc, tags, extra_kwargs)
            # 风控：发布后统计（成功计数）
            try:
                risk_control.after_publish(account["id"], ok)
            except Exception:
                pass
            if ok:
                success_count += 1
                _record_item(run_id, account, "success")
            else:
                failed_count += 1
                _record_item(run_id, account, "failed", "平台返回失败")
        except Exception as e:
            failed_count += 1
            _record_item(run_id, account, "failed", str(e))
            logger.error(f"[scheduled] 批次 账号 {account['id']} 发布失败: {e}",
                         exc_info=True)

    if success_count > 0 and failed_count == 0:
        status = "success"
    elif success_count == 0:
        status = "failed"
    else:
        status = "partial"

    _finish_run(run_id, status, success_count, failed_count)

    if success_count > 0:
        for m in task_meta:
            try:
                zr.update_publish_status(base_url, m["id"], "published")
            except Exception as e:
                logger.warning(f"[scheduled] 回写 publishStatus 失败 {m['id']}: {e}")

    _maybe_notify(rule, run_id, status, f"成功 {success_count}/{len(accounts)} 账号")


def _run_cycle(rule: dict):
    """对一个规则执行一次完整周期：拉取候选任务并逐个处理。"""
    from services import zr_task_source as zr

    base_url = _resolve_base_url(rule)
    if not base_url:
        logger.warning(f"[scheduled] 规则 {rule.get('id')} 未配置 zr_base_url，跳过")
        return

    try:
        tasks = zr.get_task_list(
            base_url,
            status=rule.get("fetch_status") or "success",
            func_type=rule.get("func_type") or "",
            prompt=rule.get("prompt") or "",
            topic=rule.get("zr_topic") or "",
            page_size=50,
        )
    except Exception as e:
        logger.error(f"[scheduled] 规则 {rule.get('id')} 拉取任务列表失败: {e}", exc_info=True)
        # 每次执行都留痕：拉取失败也写入运行记录，方便用户看到规则执行失败
        run_id = str(uuid.uuid4())
        _create_run(run_id, rule.get("id"), "", "", 0, rule.get("zr_topic") or "")
        _finish_run(run_id, "failed", 0, 0, f"拉取 ZR 任务列表失败: {e}")
        return

    candidates = [
        t for t in tasks
        if str(t.get("publishStatus") or "pending").lower() == "pending"
    ]
    logger.info(f"[scheduled] 规则 {rule.get('id')}: 候选任务 {len(candidates)} 个")
    if not candidates:
        # 无待发布任务也写入运行记录（状态 empty），让用户知道规则按时执行了、只是没货
        run_id = str(uuid.uuid4())
        _create_run(run_id, rule.get("id"), "", "", 0, rule.get("zr_topic") or "")
        msg = "ZR 无已完成任务" if not tasks else "ZR 无待发布任务（已完成任务均已发布）"
        _finish_run(run_id, "empty", 0, 0, msg)
        return

    # 发布模式：
    # - single（默认）：任务与账号按顺序配对，一个任务的全部图片发给一个账号；
    #   任务多于账号时，多余任务本次不发布（保持 pending，下个周期再进候选）。
    # - merge：每批 N 个任务合成一个发布内容做账号分层（0 = 全部候选合并为一组）。
    try:
        batch_size = int(rule.get("batch_size") or 0)
    except (TypeError, ValueError):
        batch_size = 0
    publish_mode = str(rule.get("publish_mode") or "single").strip().lower()
    if publish_mode == "merge":
        if batch_size >= 2:
            chunks = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]
            # 任务数不足每批数量 → 该批跳过（多任务就是多任务，不降级为单任务）
            chunks = [c for c in chunks if len(c) >= batch_size]
        else:
            # 0 = 全部候选合并为一组（多任务合并）；候选不足 2 个时无意义，跳过
            chunks = [candidates] if len(candidates) >= 2 else []
        paired = False
    else:
        chunks = [[t] for t in candidates]
        paired = True
    logger.info(f"[scheduled] 规则 {rule.get('id')}: 模式 {publish_mode}，切分为 {len(chunks)} 个发布内容")
    accounts = _resolve_accounts(rule)
    if paired:
        logger.info(f"[scheduled] 规则 {rule.get('id')}: 配对模式，任务按序分发到 {len(accounts)} 个账号")
    for i, chunk in enumerate(chunks):
        try:
            if paired:
                if i >= len(accounts):
                    logger.info(f"[scheduled] 规则 {rule.get('id')} 任务 {chunk[0].get('id')} "
                                f"无对应账号（任务多于账号），本次不发布")
                    continue
                _process_batch(rule, base_url, chunk, [accounts[i]])
            else:
                _process_batch(rule, base_url, chunk)
        except Exception as e:
            logger.error(f"[scheduled] 规则 {rule.get('id')} 处理任务批次异常: {e}",
                         exc_info=True)


# ── Cron 调度支持 ────────────────────────────────────────────

_WEEKDAY_NAMES = {0: "周日", 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _join_zh(items) -> str:
    """中文列表连接：两个用「和」，三个及以上用「、…和」。"""
    items = [str(x) for x in items]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]}和{items[1]}"
    return "、".join(items[:-1]) + f"和{items[-1]}"


def _expand_field(field: str, lo: int, hi: int):
    """展开 cron 字段为升序去重列表；* 或 ? 返回 None。支持 a,b / a-b / a-b/n / 单值。"""
    if field in ("*", "?"):
        return None
    out = []
    for seg in field.split(","):
        seg = seg.strip()
        if "/" in seg:
            base, step_s = seg.split("/", 1)
            step = int(step_s)
            if base in ("*", "?"):
                out.extend(range(lo, hi + 1, step))
            elif "-" in base:
                a, b = (int(x) for x in base.split("-", 1))
                out.extend(range(a, min(b, hi) + 1, step))
            else:
                out.extend(range(int(base), hi + 1, step))
        elif "-" in seg:
            a, b = (int(x) for x in seg.split("-", 1))
            out.extend(range(a, b + 1))
        else:
            out.append(int(seg))
    return sorted(set(v for v in out if lo <= v <= hi))


def _cron_time_part(minute: str, hour: str):
    """解析 分/时 两段，返回 (时间点描述, 频率描述)。"""
    m_all = minute in ("*", "?")
    h_all = hour in ("*", "?")

    if h_all and m_all:
        return "", ""
    if h_all:
        if minute.startswith("*/"):
            return "", f"每 {minute[2:]} 分钟"
        m = _expand_field(minute, 0, 59) or []
        if len(m) == 1:
            return ("", "每小时") if m[0] == 0 else ("", f"每小时 {m[0]} 分")
        return "", f"每小时 {_join_zh(m)} 分"
    if hour.startswith("*/"):
        step = hour[2:]
        m = _expand_field(minute, 0, 59) if not m_all else []
        if m_all or (len(m) == 1 and m[0] == 0):
            return "", f"每 {step} 小时"
        return "", f"每 {step} 小时 {_join_zh(m)} 分"

    # hour 为范围（如 9-18），保留区间感
    if "-" in hour and "/" not in hour and "," not in hour:
        try:
            a, b = (int(x) for x in hour.split("-", 1))
        except ValueError:
            a, b = None, None
        if a is not None and 0 <= a <= 23 and 0 <= b <= 23:
            if m_all:
                return f"{a:02d}-{b:02d} 点", ""
            if minute.startswith("*/"):
                return f"{a:02d}:00-{b:02d}:00", f"每 {minute[2:]} 分钟"
            m = _expand_field(minute, 0, 59) or []
            if len(m) == 1:
                return f"{a:02d}:{m[0]:02d}-{b:02d}:{m[0]:02d}", ""
            return f"{a:02d} 点 {_join_zh(m)} 分", ""

    h = _expand_field(hour, 0, 23) or []
    if not h:
        return "", ""
    m = _expand_field(minute, 0, 59) if not m_all else None
    if m is None:
        if len(h) == 1:
            return f"{h[0]:02d} 点", ""
        return _join_zh([f"{x:02d} 点" for x in h]), ""
    if len(m) == 1:
        if len(h) == 1:
            return f"{h[0]:02d}:{m[0]:02d}", ""
        return _join_zh([f"{x:02d}:{m[0]:02d}" for x in h]), ""
    if len(h) == 1:
        return f"{h[0]:02d} 点 {_join_zh(m)} 分", ""
    return f"{_join_zh(h)} 点 {_join_zh(m)} 分", ""


def _desc_dow(dow: str) -> str:
    if dow.startswith("*/"):
        return f"每 {dow[2:]} 天"
    vals = _expand_field(dow, 0, 7)
    if not vals:
        return ""
    keys = sorted(set(v % 7 for v in vals))
    names = [_WEEKDAY_NAMES[k] for k in keys]
    if keys == [1, 2, 3, 4, 5]:
        return "工作日"
    if keys == [0, 6]:
        return "周末"
    if len(names) == 1:
        return f"每{names[0]}"
    return f"每{_join_zh(names)}"


def _desc_dom(dom: str) -> str:
    if dom.startswith("*/"):
        return f"每 {dom[2:]} 天"
    vals = _expand_field(dom, 1, 31)
    if not vals:
        return ""
    if len(vals) == 1:
        return f"每月 {vals[0]} 日"
    return f"每月 {_join_zh(vals)} 日"


def _cron_humanize(expr: str) -> str:
    """把 5 段 cron 表达式翻译成一句话中文说明（纯展示，不影响调度）。

    支持 * 、*/n 、a-b 、a,b ；0/7 = 周日。翻译失败或非法时返回空串。
    """
    try:
        fields = (expr or "").strip().split()
        if len(fields) != 5:
            return ""
        minute, hour, dom, month, dow = fields
        time_txt, freq_txt = _cron_time_part(minute, hour)

        day_txt = ""
        d_all = dom in ("*", "?")
        w_all = dow in ("*", "?")
        if not d_all and not w_all:
            day_txt = f"{_desc_dom(dom)}或{_desc_dow(dow)}"
        elif not d_all:
            day_txt = _desc_dom(dom)
        elif not w_all:
            day_txt = _desc_dow(dow)
        if month not in ("*", "?"):
            mo = _expand_field(month, 1, 12) or []
            mo_txt = _join_zh([f"{x} 月" for x in mo])
            if day_txt.startswith("每月"):
                day_txt = f"每年 {mo_txt} {day_txt[2:].strip()}"
            else:
                day_txt = f"每年 {mo_txt} " + day_txt

        if freq_txt:
            inner = " ".join(x for x in (day_txt, time_txt) if x)
            return f"{freq_txt}（{inner}）" if inner else freq_txt
        if day_txt and time_txt:
            return f"{day_txt} {time_txt}"
        if day_txt:
            return day_txt
        if time_txt:
            return f"每天 {time_txt}"
        return "每分钟"
    except Exception:
        return ""


def _validate_cron_expr(expr: str) -> str:
    """校验 5 段 cron 表达式（分 时 日 月 周）。返回空串=合法，否则返回错误说明。"""
    expr = (expr or "").strip()
    if not expr:
        return ""
    if len(expr.split()) != 5:
        return "Cron 表达式必须为 5 段：分 时 日 月 周（例如 */30 * * * *）"
    if not _CRONITER_AVAILABLE:
        return "服务端未安装 croniter，无法使用 Cron 调度"
    try:
        _croniter(expr, datetime.now())
    except Exception as e:
        return f"Cron 表达式无效：{e}"
    return ""


def _is_due(rule: dict, now: datetime) -> bool:
    """判断规则此刻是否应该执行：按 5 段 cron 匹配，同一分钟内至多执行一次。"""
    cron_expr = (rule.get("cron_expr") or "").strip()
    if not cron_expr:
        logger.warning(f"[scheduled] 规则 {rule.get('id')} 未填写 cron 表达式，跳过")
        return False
    if len(cron_expr.split()) != 5:
        logger.warning(f"[scheduled] 规则 {rule.get('id')} cron 表达式非 5 段: {cron_expr}，跳过")
        return False
    if not _CRONITER_AVAILABLE:
        logger.warning(f"[scheduled] 规则 {rule.get('id')} 设置了 cron 但服务端未安装 croniter，跳过")
        return False
    last_raw = rule.get("last_run_at")
    last_dt = None
    if last_raw:
        try:
            last_dt = datetime.fromisoformat(last_raw)
        except ValueError:
            last_dt = None
    base = last_dt if last_dt is not None else now - timedelta(minutes=1)
    try:
        nxt = _croniter(cron_expr, base).get_next(datetime)
        return nxt <= now
    except Exception as e:
        logger.warning(f"[scheduled] 规则 {rule.get('id')} cron 解析失败: {e}，跳过")
        return False


# ── 调度线程 ────────────────────────────────────────────────

_scheduler_started = False
_inflight: set = set()
_inflight_lock = threading.Lock()


def _scheduler_loop():
    logger.info("[scheduled] 调度线程启动")
    while True:
        try:
            now = datetime.now()
            with _get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM scheduled_publish_rules WHERE enabled = 1"
                ).fetchall()
            for row in rows:
                rule = _rule_to_dict(row)
                rule_id = rule["id"]
                if not _is_due(rule, now):
                    continue
                with _inflight_lock:
                    if rule_id in _inflight:
                        continue
                    _inflight.add(rule_id)
                try:
                    _run_cycle(rule)
                    with _get_db() as conn:
                        conn.execute(
                            "UPDATE scheduled_publish_rules SET last_run_at=? WHERE id=?",
                            (datetime.now().isoformat(), rule_id),
                        )
                finally:
                    with _inflight_lock:
                        _inflight.discard(rule_id)
        except Exception as e:
            logger.error(f"[scheduled] 调度循环异常: {e}", exc_info=True)
        time.sleep(30)


def start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_scheduler_loop, daemon=True,
                     name="scheduled-publish").start()
    logger.info("[scheduled] 调度线程已启动")


def _manual_run(rule_id: str) -> bool:
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_publish_rules WHERE id=?", (rule_id,)
        ).fetchone()
    if not row:
        raise ValueError("规则不存在")
    rule = _rule_to_dict(row)
    with _inflight_lock:
        if rule_id in _inflight:
            return False
        _inflight.add(rule_id)
    try:
        _run_cycle(rule)
        with _get_db() as conn:
            conn.execute(
                "UPDATE scheduled_publish_rules SET last_run_at=? WHERE id=?",
                (datetime.now().isoformat(), rule_id),
            )
    finally:
        with _inflight_lock:
            _inflight.discard(rule_id)
    return True


# ── 路由 ────────────────────────────────────────────────────

RULE_FIELDS = (
    "name", "enabled", "zr_base_url", "workflow_id", "fetch_status", "func_type",
    "prompt", "zr_topic", "image_count", "batch_size", "publish_mode",
    "title_template", "desc_template", "tags",
    "accounts", "extra_kwargs", "cron_expr", "notify_on", "account_scope",
)


def _extract_rule_payload(data: dict) -> dict:
    payload = {}
    for f in RULE_FIELDS:
        if f in data:
            payload[f] = data[f]
    if "accounts" in payload and not isinstance(payload["accounts"], list):
        try:
            payload["accounts"] = json.loads(payload["accounts"])
        except (json.JSONDecodeError, TypeError):
            payload["accounts"] = [payload["accounts"]]
    if "extra_kwargs" in payload and isinstance(payload["extra_kwargs"], dict):
        payload["extra_kwargs"] = json.dumps(payload["extra_kwargs"], ensure_ascii=False)
    if "tags" in payload:
        _t = payload["tags"]
        if isinstance(_t, list):
            payload["tags"] = ",".join(str(x).strip() for x in _t if str(x).strip())
        elif _t is None:
            payload["tags"] = ""
    if "cron_expr" in payload:
        payload["cron_expr"] = str(payload["cron_expr"] or "").strip()
    if "publish_mode" in payload:
        pm = str(payload["publish_mode"] or "single").strip().lower()
        payload["publish_mode"] = pm if pm in ("single", "merge") else "single"
    if "account_scope" in payload:
        sc = str(payload["account_scope"] or "").strip().lower()
        payload["account_scope"] = sc if sc in ("all_normal", "all_raising", "selected") else "all_normal"
    # SQLite NOT NULL 防御：前端空字段会传 null，显式 null 会覆盖列 DEFAULT 触发 NOT NULL 约束。
    # 统一按列类型转回默认值（数字列 0，其余空串）。
    for _k in list(payload.keys()):
        if payload[_k] is None:
            payload[_k] = 0 if _k in ("image_count", "batch_size", "enabled") else ""
    return payload


@scheduled_publish_bp.route('/risk-config', methods=['GET'])
def get_risk_config():
    """读取风控配置（间隔/抖动/每日限额/失败熔断）。"""
    from services.risk_control import risk_control
    cfg = risk_control.get_config()
    with _get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='risk_publish_interval_seconds'").fetchone()
    return jsonify({"code": 200, "msg": None, "data": {
        "publish_interval_seconds": cfg["account_interval"],
        "global_interval_seconds": cfg["global_interval"],
        "jitter_seconds": cfg["jitter_seconds"],
        "daily_account_limit": cfg["daily_account_limit"],
        "daily_global_limit": cfg["daily_global_limit"],
        "fail_break_count": cfg["fail_break_count"],
        "fail_break_seconds": cfg["fail_break_seconds"],
        "configured": row is not None,
    }})


@scheduled_publish_bp.route('/risk-config', methods=['POST'])
def set_risk_config():
    """写入风控配置。body 支持（均可选）：
    publish_interval_seconds / global_interval_seconds / jitter_seconds /
    daily_account_limit / daily_global_limit / fail_break_count / fail_break_seconds
    """
    payload = request.get_json(silent=True) or {}

    def _int_field(name, lo, hi, default=None):
        if name not in payload or payload.get(name) is None:
            return default
        try:
            v = int(payload[name])
        except (TypeError, ValueError):
            raise ValueError(f"{name} 必须为整数")
        if v < lo or v > hi:
            raise ValueError(f"{name} 需在 {lo}~{hi} 之间")
        return v

    from services.risk_control import risk_control
    try:
        data = {}
        v = _int_field("publish_interval_seconds", 0, 86400)
        if v is not None:
            data["account_interval"] = v
        v = _int_field("global_interval_seconds", 0, 86400)
        if v is not None:
            data["global_interval"] = v
        v = _int_field("jitter_seconds", 0, 3600)
        if v is not None:
            data["jitter_seconds"] = v
        v = _int_field("daily_account_limit", 0, 10000)
        if v is not None:
            data["daily_account_limit"] = v
        v = _int_field("daily_global_limit", 0, 100000)
        if v is not None:
            data["daily_global_limit"] = v
        v = _int_field("fail_break_count", 0, 1000)
        if v is not None:
            data["fail_break_count"] = v
        v = _int_field("fail_break_seconds", 0, 86400 * 7)
        if v is not None:
            data["fail_break_seconds"] = v
        if not data:
            return jsonify({"code": 400, "msg": "未提供任何可保存的风控配置项", "data": None})
        cfg = risk_control.set_config(data)
    except ValueError as e:
        return jsonify({"code": 400, "msg": str(e), "data": None})
    return jsonify({"code": 200, "msg": "风控配置已保存",
                    "data": {
                        "publish_interval_seconds": cfg["account_interval"],
                        "global_interval_seconds": cfg["global_interval"],
                        "jitter_seconds": cfg["jitter_seconds"],
                        "daily_account_limit": cfg["daily_account_limit"],
                        "daily_global_limit": cfg["daily_global_limit"],
                        "fail_break_count": cfg["fail_break_count"],
                        "fail_break_seconds": cfg["fail_break_seconds"],
                    }})


@scheduled_publish_bp.route('/rules', methods=['GET'])
def list_rules():
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_publish_rules ORDER BY created_at DESC"
        ).fetchall()
    out = []
    for row in rows:
        d = _rule_to_dict(row)
        d.pop("extra_kwargs", None)
        with _get_db() as conn:
            last = conn.execute(
                "SELECT status, success_count, failed_count, started_at "
                "FROM scheduled_publish_runs WHERE rule_id=? ORDER BY started_at DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
        d["last_run"] = dict(last) if last else None
        out.append(d)
    return jsonify({"code": 200, "msg": None, "data": out})


@scheduled_publish_bp.route('/rules', methods=['POST'])
def create_rule():
    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"code": 400, "msg": "规则名称不能为空"}), 200
    rid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    payload = _extract_rule_payload(data)
    if not (payload.get("cron_expr") or "").strip():
        return jsonify({"code": 400, "msg": "请填写 Cron 表达式"}), 200
    cron_err = _validate_cron_expr(payload.get("cron_expr", ""))
    if cron_err:
        return jsonify({"code": 400, "msg": cron_err}), 200
    payload["id"] = rid
    payload["enabled"] = 1 if payload.get("enabled", True) else 0
    payload.setdefault("zr_base_url", "")
    payload.setdefault("workflow_id", "")
    payload.setdefault("fetch_status", "success")
    payload.setdefault("func_type", "")
    payload.setdefault("prompt", "")
    payload.setdefault("image_count", 0)
    payload.setdefault("batch_size", 0)
    payload.setdefault("publish_mode", "single")
    payload.setdefault("title_template", "")
    payload.setdefault("desc_template", "")
    payload.setdefault("tags", "")
    payload.setdefault("accounts", "[]")
    payload.setdefault("extra_kwargs", "{}")
    payload.setdefault("cron_expr", "")
    payload.setdefault("notify_on", "fail")
    payload.setdefault("account_scope", "all_normal")
    # accounts 在 _extract_rule_payload 中已解析为 list，存库前转回 JSON 字符串
    if isinstance(payload.get("accounts"), list):
        payload["accounts"] = json.dumps(payload["accounts"], ensure_ascii=False)
    payload["created_at"] = now
    payload["updated_at"] = now
    cols = ", ".join(payload.keys())
    marks = ", ".join(["?"] * len(payload))
    with _get_db() as conn:
        conn.execute(
            f"INSERT INTO scheduled_publish_rules ({cols}) VALUES ({marks})",
            [payload[k] for k in payload.keys()],
        )
    return jsonify({"code": 200, "msg": None, "data": {"id": rid}})


@scheduled_publish_bp.route('/rules/<rule_id>', methods=['PUT'])
def update_rule(rule_id):
    data = request.get_json() or {}
    payload = _extract_rule_payload(data)
    if not payload:
        return jsonify({"code": 400, "msg": "无更新字段"}), 200
    if "cron_expr" in payload:
        if not (payload.get("cron_expr") or "").strip():
            return jsonify({"code": 400, "msg": "请填写 Cron 表达式"}), 200
        cron_err = _validate_cron_expr(payload.get("cron_expr", ""))
        if cron_err:
            return jsonify({"code": 400, "msg": cron_err}), 200
    if "enabled" in payload:
        payload["enabled"] = 1 if payload["enabled"] else 0
    if isinstance(payload.get("accounts"), list):
        payload["accounts"] = json.dumps(payload["accounts"], ensure_ascii=False)
    payload["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in payload.keys())
    with _get_db() as conn:
        cur = conn.execute(
            f"UPDATE scheduled_publish_rules SET {sets} WHERE id=?",
            [payload[k] for k in payload.keys()] + [rule_id],
        )
        if cur.rowcount == 0:
            return jsonify({"code": 404, "msg": "规则不存在"}), 200
    return jsonify({"code": 200, "msg": None, "data": {"id": rule_id}})


@scheduled_publish_bp.route('/rules/<rule_id>', methods=['DELETE'])
def delete_rule(rule_id):
    with _get_db() as conn:
        cur = conn.execute("DELETE FROM scheduled_publish_rules WHERE id=?", (rule_id,))
        if cur.rowcount == 0:
            return jsonify({"code": 404, "msg": "规则不存在"}), 200
    return jsonify({"code": 200, "msg": None, "data": {"id": rule_id}})


@scheduled_publish_bp.route('/rules/<rule_id>/run', methods=['POST'])
def run_rule(rule_id):
    def worker():
        try:
            _manual_run(rule_id)
        except Exception as e:
            logger.error(f"[scheduled] 手动运行规则 {rule_id} 异常: {e}", exc_info=True)
    threading.Thread(target=worker, daemon=True,
                     name=f"manual-run-{rule_id}").start()
    return jsonify({"code": 200, "msg": None, "data": {"started": True}})


@scheduled_publish_bp.route('/runs', methods=['GET'])
def list_runs():
    rule_id = request.args.get("rule_id")
    limit = min(int(request.args.get("limit", 50)), 200)
    status = request.args.get("status") or ""
    days = request.args.get("days") or ""
    topic = request.args.get("topic") or ""
    sql = ("SELECT r.*, rule.name AS rule_name FROM scheduled_publish_runs r "
           "LEFT JOIN scheduled_publish_rules rule ON r.rule_id = rule.id")
    conds, args = [], []
    if rule_id:
        conds.append("r.rule_id=?")
        args.append(rule_id)
    if status:
        conds.append("r.status=?")
        args.append(status)
    if topic:
        conds.append("r.topic=?")
        args.append(topic)
    if days:
        try:
            nd = max(1, int(days))
            conds.append("r.started_at >= datetime('now', ?)")
            args.append(f"-{nd} days")
        except (TypeError, ValueError):
            pass
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY r.started_at DESC LIMIT ?"
    args.append(limit)
    with _get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        with _get_db() as conn:
            items = conn.execute(
                "SELECT * FROM scheduled_publish_items WHERE run_id=? ORDER BY rowid",
                (row["id"],),
            ).fetchall()
        item_list = [dict(i) for i in items]
        d["items"] = item_list
        # 发布统计：账号数 = 去重账号；图集数 = 明细条数（每次发布 = 一个图集内容）
        d["account_count"] = len({i.get("account_id") for i in item_list if i.get("account_id")})
        d["album_count"] = len(item_list)
        out.append(d)
    return jsonify({"code": 200, "msg": None, "data": out})


@scheduled_publish_bp.route('/runs/topics', methods=['GET'])
def list_run_topics():
    """运行记录中出现的主题列表（去重，按最近出现倒序），供前端筛选下拉。"""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT topic, MAX(started_at) AS last_ts FROM scheduled_publish_runs "
            "WHERE topic <> '' GROUP BY topic ORDER BY last_ts DESC"
        ).fetchall()
    return jsonify({"code": 200, "msg": None,
                    "data": [r["topic"] for r in rows if r["topic"]]})


@scheduled_publish_bp.route('/runs', methods=['DELETE'])
def clear_runs():
    """清空全部运行记录（级联删除明细）。"""
    with _get_db() as conn:
        conn.execute("DELETE FROM scheduled_publish_items")
        conn.execute("DELETE FROM scheduled_publish_runs")
    return jsonify({"code": 200, "msg": None, "data": {"cleared": True}})


@scheduled_publish_bp.route('/runs/<run_id>', methods=['DELETE'])
def delete_run(run_id):
    """删除单条运行记录（级联删除明细）。"""
    with _get_db() as conn:
        conn.execute("DELETE FROM scheduled_publish_items WHERE run_id=?", (run_id,))
        cur = conn.execute("DELETE FROM scheduled_publish_runs WHERE id=?", (run_id,))
        if cur.rowcount == 0:
            return jsonify({"code": 404, "msg": "运行记录不存在"}), 200
    return jsonify({"code": 200, "msg": None, "data": {"deleted": 1}})


@scheduled_publish_bp.route('/runs/<run_id>', methods=['GET'])
def get_run(run_id):
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_publish_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            return jsonify({"code": 404, "msg": "运行不存在"}), 200
        d = dict(row)
        items = conn.execute(
            "SELECT * FROM scheduled_publish_items WHERE run_id=? ORDER BY rowid",
            (run_id,),
        ).fetchall()
    d["items"] = [dict(i) for i in items]
    return jsonify({"code": 200, "msg": None, "data": d})


@scheduled_publish_bp.route('/preview', methods=['GET'])
def preview():
    rule_id = request.args.get("rule_id")
    if not rule_id:
        return jsonify({"code": 400, "msg": "缺少 rule_id"}), 200
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_publish_rules WHERE id=?", (rule_id,)
        ).fetchone()
    if not row:
        return jsonify({"code": 404, "msg": "规则不存在"}), 200
    rule = _rule_to_dict(row)
    base_url = _resolve_base_url(rule)
    if not base_url:
        return jsonify({"code": 400, "msg": "未配置 zr_base_url"}), 200
    from services import zr_task_source as zr
    try:
        tasks = zr.get_task_list(
            base_url,
            status=rule.get("fetch_status") or "success",
            func_type=rule.get("func_type") or "",
            prompt=rule.get("prompt") or "",
            topic=rule.get("zr_topic") or "",
            page_size=50,
        )
    except Exception as e:
        return jsonify({"code": 500, "msg": f"拉取任务失败: {e}"}), 200
    candidates = [
        t for t in tasks
        if str(t.get("publishStatus") or "pending").lower() == "pending"
    ]
    if not candidates:
        return jsonify({"code": 200, "msg": None,
                        "data": {"task_name": "", "title": "", "desc": "", "tags": [],
                                  "images": [], "accounts": [],
                                  "task_count": 0, "account_count": 0}})
    title, desc, tags, _ = _build_text(rule, base_url, candidates[0])
    accounts = _resolve_accounts(rule)
    try:
        batch_size = int(rule.get("batch_size") or 0)
    except (TypeError, ValueError):
        batch_size = 0
    publish_mode = str(rule.get("publish_mode") or "single").strip().lower()
    paired = publish_mode == "single" and bool(accounts)
    per_task = _batch_image_limit(rule, 1, 1) if paired         else _batch_image_limit(rule, len(candidates), len(accounts) if accounts else 1)
    task_url_lists = [zr.image_items(t.get("outputUrls"), per_task) for t in candidates]
    account_albums = []
    if paired:
        # 配对预览：账号 j 分配到候选任务 j（不足则空），展示每个账号收到的图集
        for j, account in enumerate(accounts):
            urls = task_url_lists[j] if j < len(task_url_lists) else []
            account_albums.append({
                "id": account.get("id"),
                "name": account.get("name") or "",
                "platform_id": account.get("platform_id"),
                "album": [it.get("url") for it in urls if it.get("url")],
            })
    else:
        for account in accounts:
            idx = len(account_albums)
            album = [lst[idx].get("url") for lst in task_url_lists
                     if idx < len(lst) and lst[idx].get("url")]
            account_albums.append({
                "id": account.get("id"),
                "name": account.get("name") or "",
                "platform_id": account.get("platform_id"),
                "album": album,
            })
    images = account_albums[0]["album"] if account_albums else []
    name_parts = [t.get("taskName") for t in candidates if t.get("taskName")]
    task_name = " + ".join(name_parts) if name_parts \
        else ",".join(str(t.get("id")) for t in candidates)
    return jsonify({
        "code": 200, "msg": None,
        "data": {
            "task_name": task_name,
            "task_count": len(candidates),
            "account_count": len(accounts),
            "mode": "paired" if paired else "layered",
            "title": title,
            "desc": desc,
            "tags": tags,
            "images": images,
            "accounts": account_albums,
        },
    })


@scheduled_publish_bp.route('/zr-topics', methods=['GET'])
def zr_topics():
    """从 ZR 任务列表拉取全部主题，供规则「主题过滤」下拉使用。",    支持 zr_base_url 参数覆盖全局设置。"""
    base_url = (request.args.get("zr_base_url") or "").strip()
    if not base_url:
        try:
            from impl.settings import read_settings
            base_url = read_settings().get("zr_base_url") or ""
        except Exception:
            base_url = ""
    if not base_url:
        return jsonify({"code": 400, "msg": "未配置 ZR 地址，请先在「全局设置」中配置"}), 200
    from services import zr_task_source as zr
    try:
        topics = zr.list_topics(base_url)
    except Exception as e:
        logger.error(f"[scheduled] 拉取主题列表失败: {e}")
        return jsonify({"code": 500, "msg": f"拉取主题列表失败: {e}"}), 200
    return jsonify({"code": 200, "msg": None, "data": topics}), 200


@scheduled_publish_bp.route('/zr-task-count', methods=['GET'])
def zr_task_count():
    """按当前筛选条件（主题/状态/功能类型/Prompt）统计 ZR 任务数，
    供规则表单实时提示「当前配置将匹配 N 个任务」。"""
    base_url = (request.args.get("zr_base_url") or "").strip()
    if not base_url:
        try:
            from impl.settings import read_settings
            base_url = read_settings().get("zr_base_url") or ""
        except Exception:
            base_url = ""
    if not base_url:
        return jsonify({"code": 400, "msg": "未配置 ZR 地址，请先在「全局设置」中配置"}), 200
    from services import zr_task_source as zr
    try:
        n = zr.count_tasks(
            base_url,
            status=request.args.get("status") or "",
            func_type=request.args.get("func_type") or "",
            prompt=request.args.get("prompt") or "",
            topic=request.args.get("topic") or "",
        )
    except Exception as e:
        logger.error(f"[scheduled] 统计任务数失败: {e}")
        return jsonify({"code": 500, "msg": f"统计任务数失败: {e}"}), 200
    return jsonify({"code": 200, "msg": None, "data": {"count": n}}), 200


@scheduled_publish_bp.route('/cron/next', methods=['GET'])
def cron_next():
    """前端实时预览：给定 cron 表达式，返回从当前时刻起的下次执行时间。"""
    expr = (request.args.get("expr") or "").strip()
    if not expr:
        return jsonify({"code": 400, "msg": "缺少 cron 表达式"}), 200
    if not _CRONITER_AVAILABLE:
        return jsonify({"code": 500, "msg": "服务端未安装 croniter"}), 200
    err = _validate_cron_expr(expr)
    if err:
        return jsonify({"code": 200, "msg": None,
                        "data": {"valid": False, "error": err}}), 200
    try:
        nxt = _croniter(expr, datetime.now()).get_next(datetime)
        return jsonify({"code": 200, "msg": None,
                        "data": {"valid": True, "next": nxt.isoformat(sep=" "),
                                 "desc": _cron_humanize(expr)}}), 200
    except Exception as e:
        return jsonify({"code": 200, "msg": None,
                        "data": {"valid": False, "error": str(e)}}), 200


@scheduled_publish_bp.route('/test-smtp', methods=['POST'])
def test_smtp():
    from util.mail import send_mail
    ok = send_mail("[定时图集发布] SMTP 测试", "这是一封来自 QianFanSync 的测试邮件。")
    return jsonify({"code": 200 if ok else 500,
                    "msg": None if ok else "SMTP 发送失败，请检查设置",
                    "data": {"ok": ok}})


@scheduled_publish_bp.route('/test-zr', methods=['POST'])
def test_zr():
    data = request.get_json() or {}
    base_url = (data.get("zr_base_url") or "").strip()
    if not base_url:
        try:
            from impl.settings import read_settings
            base_url = read_settings().get("zr_base_url") or ""
        except Exception:
            pass
    if not base_url:
        return jsonify({"code": 400, "msg": "未配置 zr_base_url"}), 200
    from services import zr_task_source as zr
    try:
        tasks = zr.get_task_list(base_url, status=data.get("status") or "success",
                              topic=data.get("topic") or "",
                                 page_size=5)
        return jsonify({"code": 200, "msg": None,
                        "data": {"ok": True, "count": len(tasks),
                                  "tasks": [{"id": t.get("id"), "name": t.get("taskName")} for t in tasks]}})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"连接 ZR 失败: {e}", "data": {"ok": False}})
