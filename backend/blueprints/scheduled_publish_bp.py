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
from datetime import datetime
from pathlib import Path

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


def _resolve_accounts(rule: dict) -> list:
    """解析规则目标账号：留空时返回全部账号（按 id 稳定排序，逐一发布），否则按所选 id 取。"""
    selected = rule.get("accounts") or []
    if selected:
        return _get_accounts(selected)
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, type AS platform_id, filePath AS cookie_path, userName AS name "
            "FROM user_info ORDER BY id"
        ).fetchall()
    return [{
        "id": row["id"],
        "platform_id": row["platform_id"],
        "cookie_path": row["cookie_path"],
        "name": row["name"],
    } for row in rows]


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


def _create_run(run_id, rule_id, task_id, task_name, total_images):
    now = datetime.now().isoformat()
    with _get_db() as conn:
        conn.execute(
            """INSERT INTO scheduled_publish_runs
               (id, rule_id, task_id, task_name, status, total_images,
                success_count, failed_count, started_at, error_message)
               VALUES (?, ?, ?, ?, 'running', ?, 0, 0, ?, '')""",
            (run_id, rule_id, str(task_id), task_name or "", total_images, now),
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

    title = zr.resolve_placeholders(rule.get("title_template") or "", variable_values, labels)
    desc = zr.resolve_placeholders(rule.get("desc_template") or "", variable_values, labels)
    tags_raw = rule.get("tags") or ""
    if isinstance(tags_raw, str):
        tags = [x.strip() for x in tags_raw.split(",") if x.strip()]
    else:
        tags = list(tags_raw)
    extra_kwargs = rule.get("extra_kwargs") or {}
    return title, desc, tags, extra_kwargs


def _process_batch(rule: dict, base_url: str, tasks: list):
    """处理一组有序任务：第 i 个账号 = 按任务顺序，取每个任务的第 i 张图片拼成图集发布。

    - 每个任务一个子目录下载，避免跨任务文件名冲突。
    - 某任务图片不足第 i 张时，该账号图集跳过该任务（不报错）。
    - 标题/描述用第一个任务的变量解析，所有账号共用。
    - 有任一账号成功即把全部任务回写 publishStatus=published。
    """
    from services import zr_task_source as zr

    if not tasks:
        return
    accounts = _resolve_accounts(rule)
    if not accounts:
        logger.warning(f"[scheduled] 规则 {rule.get('id')} 无有效账号，跳过")
        return

    run_id = str(uuid.uuid4())
    dest_dir = DOWNLOAD_ROOT / run_id
    max_imgs = max(1, len(accounts))

    task_images: list = []
    task_meta: list = []
    for t in tasks:
        t_id = str(t.get("id"))
        sub = dest_dir / t_id
        local_paths = zr.download_images(base_url, t.get("outputUrls"), sub, max_imgs)
        task_images.append(local_paths)
        task_meta.append({"id": t_id, "name": t.get("taskName") or ""})
        logger.info(f"[scheduled] 规则 {rule.get('id')} 任务 {t_id} 下载 {len(local_paths)} 张")

    title, desc, tags, extra_kwargs = _build_text(rule, base_url, tasks[0])

    task_id_repr = ",".join(m["id"] for m in task_meta)
    name_parts = [m["name"] for m in task_meta if m["name"]]
    task_name_repr = " + ".join(name_parts) if name_parts else task_id_repr
    total_images = sum(len(x) for x in task_images)
    _create_run(run_id, rule.get("id"), task_id_repr, task_name_repr, total_images)

    success_count = 0
    failed_count = 0
    for idx, account in enumerate(accounts):
        album = [imgs[idx] for imgs in task_images if idx < len(imgs)]
        if not album:
            failed_count += 1
            _record_item(run_id, account, "failed", f"图片不足（第 {idx + 1} 层无图）")
            continue
        try:
            ok = _do_publish_one(account, album, title, desc, tags, extra_kwargs)
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
            page_size=50,
        )
    except Exception as e:
        logger.error(f"[scheduled] 规则 {rule.get('id')} 拉取任务列表失败: {e}", exc_info=True)
        return

    candidates = [
        t for t in tasks
        if str(t.get("publishStatus") or "pending").lower() == "pending"
    ]
    logger.info(f"[scheduled] 规则 {rule.get('id')}: 候选任务 {len(candidates)} 个")
    if not candidates:
        return

    # 按「每批任务数」切分发布内容：N>=1 时每 N 个任务合成一个发布内容，各自独立做账号分层循环；
    # 0 = 全部候选任务合成一个发布内容（兼容旧行为）。
    try:
        batch_size = int(rule.get("batch_size") or 0)
    except (TypeError, ValueError):
        batch_size = 0
    if batch_size >= 1:
        chunks = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]
    else:
        chunks = [candidates]
    logger.info(f"[scheduled] 规则 {rule.get('id')}: 切分为 {len(chunks)} 个发布内容")
    for chunk in chunks:
        try:
            _process_batch(rule, base_url, chunk)
        except Exception as e:
            logger.error(f"[scheduled] 规则 {rule.get('id')} 处理任务批次异常: {e}",
                         exc_info=True)


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
                interval = max(1, int(rule.get("interval_minutes") or 30))
                last = rule.get("last_run_at")
                if last:
                    try:
                        last_dt = datetime.fromisoformat(last)
                        if (now - last_dt).total_seconds() < interval * 60:
                            continue
                    except ValueError:
                        pass
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
    "prompt", "image_count", "batch_size", "title_template", "desc_template", "tags",
    "accounts", "extra_kwargs", "interval_minutes", "notify_on",
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
    return payload


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
    payload["id"] = rid
    payload["enabled"] = 1 if payload.get("enabled", True) else 0
    payload.setdefault("zr_base_url", "")
    payload.setdefault("workflow_id", "")
    payload.setdefault("fetch_status", "success")
    payload.setdefault("func_type", "")
    payload.setdefault("prompt", "")
    payload.setdefault("image_count", 0)
    payload.setdefault("batch_size", 0)
    payload.setdefault("title_template", "")
    payload.setdefault("desc_template", "")
    payload.setdefault("tags", "")
    payload.setdefault("accounts", "[]")
    payload.setdefault("extra_kwargs", "{}")
    payload.setdefault("interval_minutes", 30)
    payload.setdefault("notify_on", "fail")
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
    if "enabled" in payload:
        payload["enabled"] = 1 if payload["enabled"] else 0
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
    with _get_db() as conn:
        if rule_id:
            rows = conn.execute(
                "SELECT * FROM scheduled_publish_runs WHERE rule_id=? "
                "ORDER BY started_at DESC LIMIT ?", (rule_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_publish_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        with _get_db() as conn:
            items = conn.execute(
                "SELECT * FROM scheduled_publish_items WHERE run_id=? ORDER BY rowid",
                (row["id"],),
            ).fetchall()
        d["items"] = [dict(i) for i in items]
        out.append(d)
    return jsonify({"code": 200, "msg": None, "data": out})


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
    max_imgs = max(1, len(accounts)) if accounts else 1
    task_url_lists = [zr.image_items(t.get("outputUrls"), max_imgs) for t in candidates]
    account_albums = []
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
            "title": title,
            "desc": desc,
            "tags": tags,
            "images": images,
            "accounts": account_albums,
        },
    })


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
                                 page_size=5)
        return jsonify({"code": 200, "msg": None,
                        "data": {"ok": True, "count": len(tasks),
                                  "tasks": [{"id": t.get("id"), "name": t.get("taskName")} for t in tasks]}})
    except Exception as e:
        return jsonify({"code": 500, "msg": f"连接 ZR 失败: {e}", "data": {"ok": False}})
