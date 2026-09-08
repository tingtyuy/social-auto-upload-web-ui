"""定时图集发布：cron 调度支持测试。

覆盖：
- _validate_cron_expr：5 段校验、字段范围校验、空串/非法/段数错误
- _is_due：cron 命中/未命中/同分钟防重/下次触发/缺失或非法 cron 不执行
- 接口：创建规则 cron 必填、非法 cron 返回 400、只改 enabled 不受影响、/cron/next 预览
"""
import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_tmpdir = tempfile.mkdtemp()
os.environ['SAU_DATA_DIR'] = _tmpdir
DB_PATH = Path(_tmpdir) / "db" / "database.db"

_RULES_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_publish_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    zr_base_url TEXT NOT NULL DEFAULT '',
    workflow_id TEXT NOT NULL DEFAULT '',
    fetch_status TEXT NOT NULL DEFAULT 'success',
    func_type TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    zr_topic TEXT NOT NULL DEFAULT '',
    image_count INTEGER NOT NULL DEFAULT 0,
    batch_size INTEGER NOT NULL DEFAULT 0,
    publish_mode TEXT NOT NULL DEFAULT 'single',
    title_template TEXT NOT NULL DEFAULT '',
    desc_template TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    accounts TEXT NOT NULL DEFAULT '[]',
    extra_kwargs TEXT NOT NULL DEFAULT '{}',
    cron_expr TEXT NOT NULL DEFAULT '',
    notify_on TEXT NOT NULL DEFAULT 'fail',
    last_run_at TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS scheduled_publish_runs (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    task_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    total_images INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scheduled_publish_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    account_id INTEGER,
    account_name TEXT NOT NULL DEFAULT '',
    platform_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT ''
);
"""


def _setup():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(_RULES_SCHEMA)
    conn.commit()
    conn.close()


class TestCronValidate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup()
        from blueprints.scheduled_publish_bp import _validate_cron_expr
        cls.validate = staticmethod(_validate_cron_expr)

    def test_empty_ok(self):
        self.assertEqual(self.validate(""), "")
        self.assertEqual(self.validate("   "), "")

    def test_valid_expressions(self):
        for expr in (
            "0 9 * * *",
            "*/30 * * * *",
            "0 */2 * * *",
            "0 9,18 * * *",
            "30 8 * * 1",
            "0 8 * * 1-5",
            "0 10 * * 0,6",
            "0 8 1 * *",
            "*/30 9-18 * * 1-5",
            "0 0 13 * 5",
        ):
            self.assertEqual(self.validate(expr), "", expr)

    def test_invalid_expressions(self):
        for expr in (
            "99 25 * * *",   # 分钟/小时越界
            "0 9 * *",       # 只有 4 段
            "0 0 * * * *",   # 6 段（含秒），本系统只支持 5 段
            "a b c d e",     # 非数字
            "0 9 * * 8",     # 星期越界（0-7）
        ):
            self.assertNotEqual(self.validate(expr), "", expr)

    def test_whitespace_stripped(self):
        self.assertEqual(self.validate("  0 9 * * *  "), "")


class TestIsDue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup()
        from blueprints.scheduled_publish_bp import _is_due
        cls.is_due = staticmethod(_is_due)

    def test_missing_cron_not_due(self):
        now = datetime(2026, 9, 7, 12, 0, 5)
        # 未填 cron → 不执行
        self.assertFalse(self.is_due({"cron_expr": "", "last_run_at": ""}, now))
        self.assertFalse(self.is_due({"cron_expr": None, "last_run_at": ""}, now))

    def test_invalid_cron_not_due(self):
        now = datetime(2026, 9, 7, 12, 0, 5)
        # 非法 cron → 不执行
        self.assertFalse(self.is_due({"cron_expr": "99 99 * * *", "last_run_at": ""}, now))
        # 6 段 → 不执行
        self.assertFalse(self.is_due({"cron_expr": "0 0 * * * *", "last_run_at": ""}, now))

    def test_cron_first_tick(self):
        # 从未运行，cron */30 在 12:30 命中 → 到期
        now = datetime(2026, 9, 7, 12, 30, 5)
        rule = {"cron_expr": "*/30 * * * *", "last_run_at": ""}
        self.assertTrue(self.is_due(rule, now))
        # 12:15 未命中 → 未到期
        now = datetime(2026, 9, 7, 12, 15, 5)
        self.assertFalse(self.is_due(rule, now))

    def test_cron_same_minute_no_double_run(self):
        # 12:30:00 已运行，12:30:35 同一分钟内不再触发
        now = datetime(2026, 9, 7, 12, 30, 35)
        rule = {"cron_expr": "*/30 * * * *",
                "last_run_at": datetime(2026, 9, 7, 12, 30, 0).isoformat()}
        self.assertFalse(self.is_due(rule, now))
        # 13:00:05 下一个整点 → 到期
        now = datetime(2026, 9, 7, 13, 0, 5)
        self.assertTrue(self.is_due(rule, now))

    def test_cron_daily(self):
        # 每天 9 点：今天 9 点跑过后，10 点不再触发
        now = datetime(2026, 9, 7, 10, 0, 5)
        rule = {"cron_expr": "0 9 * * *",
                "last_run_at": datetime(2026, 9, 7, 9, 0, 10).isoformat()}
        self.assertFalse(self.is_due(rule, now))
        # 次日 9:00:05 → 到期
        now = datetime(2026, 9, 8, 9, 0, 5)
        self.assertTrue(self.is_due(rule, now))


class TestCronEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup()
        # 钉住蓝图使用的 DB，避免受其他测试模块 SAU_DATA_DIR 导入顺序影响
        from blueprints import scheduled_publish_bp as _bp
        _bp.DB_PATH = DB_PATH
        from app import app
        cls.client = app.test_client()

    def test_create_rule_with_cron(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "cron 测试规则",
            "cron_expr": "0 9,18 * * *",
            "enabled": True,
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 200, data)
        rid = data["data"]["id"]
        # 读回确认 cron_expr 持久化
        lst = self.client.get('/api/scheduled-publish/rules').get_json()
        row = next(r for r in lst["data"] if r["id"] == rid)
        self.assertEqual(row["cron_expr"], "0 9,18 * * *")
        # 字段中不再包含 interval_minutes
        self.assertNotIn("interval_minutes", row)

    def test_create_rule_default_mode_single(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "默认模式规则",
            "cron_expr": "0 9 * * *",
            "enabled": True,
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 200, data)
        rid = data["data"]["id"]
        lst = self.client.get('/api/scheduled-publish/rules').get_json()
        row = next(r for r in lst["data"] if r["id"] == rid)
        self.assertEqual(row["publish_mode"], "single")

    def test_create_rule_merge_mode(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "合并模式规则",
            "cron_expr": "0 9 * * *",
            "publish_mode": "merge",
            "batch_size": 2,
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 200, data)
        rid = data["data"]["id"]
        lst = self.client.get('/api/scheduled-publish/rules').get_json()
        row = next(r for r in lst["data"] if r["id"] == rid)
        self.assertEqual(row["publish_mode"], "merge")
        self.assertEqual(row["batch_size"], 2)

    def test_create_rule_invalid_mode_fallback(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "非法模式规则",
            "cron_expr": "0 9 * * *",
            "publish_mode": "weird",
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 200, data)
        rid = data["data"]["id"]
        lst = self.client.get('/api/scheduled-publish/rules').get_json()
        row = next(r for r in lst["data"] if r["id"] == rid)
        self.assertEqual(row["publish_mode"], "single")

    def test_update_rule_mode_only(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "模式切换规则",
            "cron_expr": "0 9 * * *",
        })
        rid = resp.get_json()["data"]["id"]
        # 仅更新 publish_mode，不携带 cron_expr，不应被 cron 校验拦截
        resp = self.client.put(f'/api/scheduled-publish/rules/{rid}', json={
            "publish_mode": "merge",
            "batch_size": 3,
        })
        self.assertEqual(resp.get_json()["code"], 200)
        lst = self.client.get('/api/scheduled-publish/rules').get_json()
        row = next(r for r in lst["data"] if r["id"] == rid)
        self.assertEqual(row["publish_mode"], "merge")

    def test_create_rule_without_cron_rejected(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "无 cron 规则",
            "enabled": True,
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 400, data)
        self.assertIn("Cron", data["msg"])

    def test_create_rule_invalid_cron_rejected(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "坏 cron",
            "cron_expr": "99 99 * * *",
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 400, data)
        self.assertIn("Cron", data["msg"])

    def test_create_rule_six_field_rejected(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "6 段 cron",
            "cron_expr": "0 0 * * * *",
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 400, data)
        self.assertIn("5 段", data["msg"])

    def test_update_rule_cron(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "待更新规则", "cron_expr": "0 9 * * *",
        })
        rid = resp.get_json()["data"]["id"]
        resp = self.client.put(f'/api/scheduled-publish/rules/{rid}', json={
            "cron_expr": "30 8 * * 1",
        })
        self.assertEqual(resp.get_json()["code"], 200)
        lst = self.client.get('/api/scheduled-publish/rules').get_json()
        row = next(r for r in lst["data"] if r["id"] == rid)
        self.assertEqual(row["cron_expr"], "30 8 * * 1")

    def test_update_rule_invalid_cron_rejected(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "再更新", "cron_expr": "0 9 * * *",
        })
        rid = resp.get_json()["data"]["id"]
        resp = self.client.put(f'/api/scheduled-publish/rules/{rid}', json={
            "cron_expr": "0 9 * *",
        })
        self.assertEqual(resp.get_json()["code"], 400)

    def test_update_rule_empty_cron_rejected(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "清空 cron", "cron_expr": "0 9 * * *",
        })
        rid = resp.get_json()["data"]["id"]
        resp = self.client.put(f'/api/scheduled-publish/rules/{rid}', json={
            "cron_expr": "",
        })
        self.assertEqual(resp.get_json()["code"], 400)

    def test_update_enabled_only_without_cron_ok(self):
        # 列表页开关只发 {enabled}，不应触发 cron 必填校验
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "开关规则", "cron_expr": "0 9 * * *",
        })
        rid = resp.get_json()["data"]["id"]
        resp = self.client.put(f'/api/scheduled-publish/rules/{rid}', json={
            "enabled": False,
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 200, data)
        lst = self.client.get('/api/scheduled-publish/rules').get_json()
        row = next(r for r in lst["data"] if r["id"] == rid)
        self.assertFalse(row["enabled"])

    def test_cron_next_endpoint(self):
        resp = self.client.get('/api/scheduled-publish/cron/next', query_string={
            "expr": "0 9 * * *",
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 200)
        self.assertTrue(data["data"]["valid"])
        nxt = datetime.fromisoformat(data["data"]["next"])
        self.assertGreater(nxt, datetime.now())
        self.assertEqual(nxt.minute, 0)
        self.assertEqual(nxt.hour, 9)
        self.assertEqual(data["data"]["desc"], "每天 09:00")

    def test_cron_humanize_common(self):
        from blueprints.scheduled_publish_bp import _cron_humanize
        cases = {
            "*/30 * * * *": "每 30 分钟",
            "0 * * * *": "每小时",
            "0 */2 * * *": "每 2 小时",
            "0 9 * * *": "每天 09:00",
            "0 9,18 * * *": "每天 09:00和18:00",
            "30 8 * * 1": "每周一 08:30",
            "0 8 * * 1-5": "工作日 08:00",
            "0 10 * * 0,6": "周末 10:00",
            "0 8 1 * *": "每月 1 日 08:00",
            "*/30 9-18 * * 1-5": "每 30 分钟（工作日 09:00-18:00）",
            "* * * * *": "每分钟",
            "30 22 * * 3,5": "每周三和周五 22:30",
            "0 12 */2 * *": "每 2 天 12:00",
            "15 * * * *": "每小时 15 分",
            "30 8 1 3 *": "每年 3 月 1 日 08:30",
        }
        for expr, want in cases.items():
            self.assertEqual(_cron_humanize(expr), want, expr)

    def test_cron_humanize_invalid(self):
        from blueprints.scheduled_publish_bp import _cron_humanize
        self.assertEqual(_cron_humanize(""), "")
        self.assertEqual(_cron_humanize("0 9 * *"), "")
        self.assertEqual(_cron_humanize("a b c d e"), "")

    def test_batch_image_limit(self):
        from blueprints.scheduled_publish_bp import _batch_image_limit
        # 单任务批次：0 = 取该任务全部图片；>0 = 上限
        self.assertEqual(_batch_image_limit({"image_count": 0}, 1, 3), 0)
        self.assertEqual(_batch_image_limit({"image_count": 5}, 1, 3), 5)
        self.assertEqual(_batch_image_limit({"image_count": None}, 1, 1), 0)
        self.assertEqual(_batch_image_limit({"image_count": "bad"}, 1, 1), 0)
        # 多任务批次：按账号数分层取图，受图片数量上限约束
        self.assertEqual(_batch_image_limit({"image_count": 0}, 3, 3), 3)
        self.assertEqual(_batch_image_limit({"image_count": 0}, 3, 1), 1)
        self.assertEqual(_batch_image_limit({"image_count": 2}, 3, 3), 2)
        self.assertEqual(_batch_image_limit({"image_count": 9}, 3, 3), 3)

    def test_cron_next_invalid(self):
        resp = self.client.get('/api/scheduled-publish/cron/next', query_string={
            "expr": "bad expr here",
        })
        data = resp.get_json()
        self.assertEqual(data["code"], 200)
        self.assertFalse(data["data"]["valid"])

    def test_cron_next_missing(self):
        resp = self.client.get('/api/scheduled-publish/cron/next')
        self.assertEqual(resp.get_json()["code"], 400)


class TestTopicFilter(unittest.TestCase):
    """ZR 任务主题过滤（zr_topic）测试。"""

    @classmethod
    def setUpClass(cls):
        _setup()
        from blueprints import scheduled_publish_bp as _bp
        _bp.DB_PATH = DB_PATH
        from app import app
        cls.client = app.test_client()

    def test_extract_payload_keeps_zr_topic(self):
        from blueprints.scheduled_publish_bp import _extract_rule_payload
        payload = _extract_rule_payload({"name": "r", "zr_topic": "山海经", "prompt": "p"})
        self.assertEqual(payload["zr_topic"], "山海经")
        self.assertEqual(payload["prompt"], "p")
        # 不传时缺省
        payload2 = _extract_rule_payload({"name": "r"})
        self.assertNotIn("zr_topic", payload2)

    def test_create_rule_with_zr_topic(self):
        resp = self.client.post('/api/scheduled-publish/rules', json={
            "name": "topic-rule",
            "cron_expr": "0 9 * * *",
            "zr_topic": "城市夜景",
        })
        self.assertEqual(resp.get_json()["code"], 200)
        rid = resp.get_json()["data"]["id"]
        r = self.client.get('/api/scheduled-publish/rules').get_json()["data"]
        rule = next(x for x in r if x["id"] == rid)
        self.assertEqual(rule["zr_topic"], "城市夜景")

    def test_get_task_list_maps_qianfan_status_to_zr(self):
        """QianFan 语义 success/running 映射到 ZR 状态 done/processing。"""
        import services.zr_task_source as zr
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"code": 200, "data": {"result": [], "totalNum": 0}}

        def fake_get(url, params=None, timeout=None):
            captured["params"] = params or {}
            return FakeResp()

        old = zr.requests.get
        zr.requests.get = fake_get
        try:
            zr.get_task_list("http://zr:8888", status="success")
            self.assertEqual(captured["params"].get("status"), "done")
            zr.get_task_list("http://zr:8888", status="running")
            self.assertEqual(captured["params"].get("status"), "processing")
            zr.get_task_list("http://zr:8888", status="failed")
            self.assertEqual(captured["params"].get("status"), "failed")
        finally:
            zr.requests.get = old

    def test_get_task_list_passes_topic(self):
        import services.zr_task_source as zr
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"code": 200, "data": {"result": [], "totalNum": 0}}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params or {}
            return FakeResp()

        old = zr.requests.get
        zr.requests.get = fake_get
        try:
            zr.get_task_list("http://zr:8888", status="success", topic="山海经")
        finally:
            zr.requests.get = old
        self.assertIn("/comfyui/task/list", captured["url"])
        self.assertEqual(captured["params"].get("topic"), "山海经")

    def test_get_task_list_without_topic(self):
        import services.zr_task_source as zr
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"code": 200, "data": {"result": [], "totalNum": 0}}

        def fake_get(url, params=None, timeout=None):
            captured["params"] = params or {}
            return FakeResp()

        old = zr.requests.get
        zr.requests.get = fake_get
        try:
            zr.get_task_list("http://zr:8888", status="success")
        finally:
            zr.requests.get = old
        self.assertNotIn("topic", captured["params"])


if __name__ == "__main__":
    unittest.main()
