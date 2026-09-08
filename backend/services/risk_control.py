# -*- coding: utf-8 -*-
"""
risk_control.py — 发布风控模块（可扩展框架）
================================================
统一拦截/控制对外发布行为，避免多账号连续高频发布触发平台风控。
通过「间隔 + 随机抖动 + 每日限额 + 失败熔断」模拟人类发布节奏，降低机器特征。

当前能力：
- before_publish：发布前检查，返回 (allowed, waited_seconds, reason)。
  * 间隔控制：距该账号上次发布不足「同账号间隔」、或距任意发布不足「全局间隔」时
    自动等待（预约式：等待完成后立即占用时间槽，防并发/重试绕过）。
  * 随机抖动：实际等待 = 基础间隔 + 随机抖动，避免固定间隔暴露机器特征。
  * 每日限额：每账号 / 全局每日发布次数上限（超限跳过本次）。
  * 失败熔断：账号连续失败达到阈值后，暂停该账号一段时间（风控前兆保护）。
- after_publish：发布后统计（成功计数 + 失败计数，按自然日），预留扩展点。

配置（settings 表，可通过 /api/scheduled-publish/risk-config 读写）：
- risk_publish_interval_seconds: 同一账号两次发布的最小间隔（秒），默认 60。
- risk_global_interval_seconds: 任意两次发布之间的全局最小间隔（秒），默认 30。
- risk_interval_jitter_seconds: 间隔随机抖动上限（秒），默认 20（等待在 [base, base+jitter]）。
- risk_daily_account_limit: 每账号每日发布次数上限，0=不限，默认 0。
- risk_daily_global_limit: 全局每日发布次数上限，0=不限，默认 0。
- risk_fail_break_count: 连续失败 N 次触发熔断，0=关闭，默认 3。
- risk_fail_break_seconds: 熔断暂停时长（秒），默认 600。

状态（risk_state 表）：
- account_id -> last_publish_time / publish_count / date / failed_count / last_failure_time；
  全局槽用 account_id='__global__' 单行记录（last_publish_time + publish_count + date）。
"""
import random
import sqlite3
import threading
import time
from pathlib import Path

_lock = threading.Lock()

# 全局默认配置
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_GLOBAL_INTERVAL_SECONDS = 30
DEFAULT_JITTER_SECONDS = 20
DEFAULT_DAILY_ACCOUNT_LIMIT = 0
DEFAULT_DAILY_GLOBAL_LIMIT = 0
DEFAULT_FAIL_BREAK_COUNT = 3
DEFAULT_FAIL_BREAK_SECONDS = 600

# settings 表 key
SETTING_INTERVAL_KEY = "risk_publish_interval_seconds"
SETTING_GLOBAL_INTERVAL_KEY = "risk_global_interval_seconds"
SETTING_JITTER_KEY = "risk_interval_jitter_seconds"
SETTING_DAILY_ACCOUNT_KEY = "risk_daily_account_limit"
SETTING_DAILY_GLOBAL_KEY = "risk_daily_global_limit"
SETTING_FAIL_BREAK_COUNT_KEY = "risk_fail_break_count"
SETTING_FAIL_BREAK_SECONDS_KEY = "risk_fail_break_seconds"

# 全局槽 account_id
GLOBAL_SLOT = "__global__"


def _default_db_path():
    try:
        import conf
        return str(Path(conf.BASE_DIR) / "db" / "database.db")
    except Exception:
        # 兜底：相对于当前工作目录
        return str(Path("data") / "db" / "database.db")


class RiskControl:
    """发布风控入口。进程内单例（risk_control），状态持久化到 SQLite。"""

    def __init__(self, db_path=None):
        self._db_path = db_path or _default_db_path()
        self._ensure_table()

    # ── 表结构 ──────────────────────────────────────────────
    def _ensure_table(self):
        try:
            with sqlite3.connect(self._db_path) as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS risk_state (
                        account_id TEXT PRIMARY KEY,
                        last_publish_time REAL NOT NULL DEFAULT 0,
                        publish_count INTEGER NOT NULL DEFAULT 0,
                        date TEXT NOT NULL DEFAULT '',
                        failed_count INTEGER NOT NULL DEFAULT 0,
                        last_failure_time REAL NOT NULL DEFAULT 0
                    )
                """)
                # 老表迁移：补充失败熔断字段
                for col, ddl in (
                    ("failed_count", "INTEGER NOT NULL DEFAULT 0"),
                    ("last_failure_time", "REAL NOT NULL DEFAULT 0"),
                ):
                    try:
                        c.execute(f"ALTER TABLE risk_state ADD COLUMN {col} {ddl}")
                    except sqlite3.OperationalError:
                        pass  # 列已存在
        except Exception:
            # init_db 已兜底建表；此处失败不阻断发布（风控降级为不等待）
            pass

    # ── 配置 ────────────────────────────────────────────────
    def _read_setting(self, key, default):
        try:
            with sqlite3.connect(self._db_path) as c:
                row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row and row[0] is not None:
                v = float(str(row[0]).strip())
                if v >= 0:
                    return v
        except Exception:
            pass
        return default

    def _write_setting(self, key, value):
        with sqlite3.connect(self._db_path) as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(max(0, float(value)))),
            )

    def get_config(self) -> dict:
        """读取全部风控配置。"""
        return {
            "account_interval": int(self._read_setting(SETTING_INTERVAL_KEY, DEFAULT_INTERVAL_SECONDS)),
            "global_interval": int(self._read_setting(SETTING_GLOBAL_INTERVAL_KEY, DEFAULT_GLOBAL_INTERVAL_SECONDS)),
            "jitter_seconds": int(self._read_setting(SETTING_JITTER_KEY, DEFAULT_JITTER_SECONDS)),
            "daily_account_limit": int(self._read_setting(SETTING_DAILY_ACCOUNT_KEY, DEFAULT_DAILY_ACCOUNT_LIMIT)),
            "daily_global_limit": int(self._read_setting(SETTING_DAILY_GLOBAL_KEY, DEFAULT_DAILY_GLOBAL_LIMIT)),
            "fail_break_count": int(self._read_setting(SETTING_FAIL_BREAK_COUNT_KEY, DEFAULT_FAIL_BREAK_COUNT)),
            "fail_break_seconds": int(self._read_setting(SETTING_FAIL_BREAK_SECONDS_KEY, DEFAULT_FAIL_BREAK_SECONDS)),
        }

    def get_interval_seconds(self) -> tuple:
        """兼容旧调用：返回 (同账号间隔, 全局间隔)。"""
        cfg = self.get_config()
        return cfg["account_interval"], cfg["global_interval"]

    def set_interval_seconds(self, account_seconds, global_seconds=None) -> None:
        """兼容旧调用：只写两个间隔。"""
        self._write_setting(SETTING_INTERVAL_KEY, account_seconds)
        if global_seconds is not None:
            self._write_setting(SETTING_GLOBAL_INTERVAL_KEY, global_seconds)

    def set_config(self, cfg: dict) -> dict:
        """写配置（部分字段可缺省）。返回合并后的最新配置。"""
        if "account_interval" in cfg or "publish_interval_seconds" in cfg:
            self._write_setting(SETTING_INTERVAL_KEY, cfg.get("account_interval", cfg.get("publish_interval_seconds")))
        if "global_interval" in cfg or "global_interval_seconds" in cfg:
            self._write_setting(SETTING_GLOBAL_INTERVAL_KEY, cfg.get("global_interval", cfg.get("global_interval_seconds")))
        for key, ck in (
            (SETTING_JITTER_KEY, "jitter_seconds"),
            (SETTING_DAILY_ACCOUNT_KEY, "daily_account_limit"),
            (SETTING_DAILY_GLOBAL_KEY, "daily_global_limit"),
            (SETTING_FAIL_BREAK_COUNT_KEY, "fail_break_count"),
            (SETTING_FAIL_BREAK_SECONDS_KEY, "fail_break_seconds"),
        ):
            if ck in cfg:
                self._write_setting(key, cfg[ck])
        return self.get_config()

    # ── 发布钩子 ────────────────────────────────────────────
    def before_publish(self, account_id) -> tuple:
        """发布前调用。返回 (allowed, waited_seconds, reason)。

        - allowed=False：本次不应发布（每日限额已满 / 失败熔断中），reason 说明原因。
        - allowed=True：已按「同账号/全局间隔 + 随机抖动」等待完成并占用时间槽。
        """
        cfg = self.get_config()
        account_id = str(account_id)
        waited = 0.0
        with _lock:
            try:
                import datetime
                today = datetime.date.today().isoformat()
                now = time.time()
                with sqlite3.connect(self._db_path, timeout=10) as c:
                    def _row(aid):
                        r = c.execute(
                            "SELECT last_publish_time, publish_count, date, failed_count, last_failure_time "
                            "FROM risk_state WHERE account_id=?", (aid,)
                        ).fetchone()
                        return r

                    acc_row = _row(account_id)
                    g_row = _row(GLOBAL_SLOT)

                    # 1) 每账号每日限额
                    dlimit = cfg["daily_account_limit"]
                    if dlimit > 0 and acc_row and acc_row[2] == today and (acc_row[1] or 0) >= dlimit:
                        return False, 0.0, f"该账号今日已达发布上限 {dlimit} 次"

                    # 2) 全局每日限额
                    gdlimit = cfg["daily_global_limit"]
                    if gdlimit > 0 and g_row and g_row[2] == today and (g_row[1] or 0) >= gdlimit:
                        return False, 0.0, f"今日全局已达发布上限 {gdlimit} 次"

                    # 3) 连续失败熔断
                    if cfg["fail_break_count"] > 0 and cfg["fail_break_seconds"] > 0 and acc_row:
                        fc = acc_row[3] or 0
                        lft = acc_row[4] or 0
                        if fc >= cfg["fail_break_count"] and lft > 0:
                            remain = cfg["fail_break_seconds"] - (now - lft)
                            if remain > 0:
                                return False, 0.0, (
                                    f"连续失败 {fc} 次，账号熔断中（剩约 {int(remain)} 秒）")

                    # 4) 间隔等待（基础间隔 + 随机抖动，模拟人类节奏）
                    acc_last = float(acc_row[0]) if acc_row and acc_row[0] else 0.0
                    glast = float(g_row[0]) if g_row and g_row[0] else 0.0
                    jitter = random.uniform(0, cfg["jitter_seconds"]) if cfg["jitter_seconds"] > 0 else 0.0
                    wait = max(
                        acc_last + cfg["account_interval"] + jitter - now,
                        glast + cfg["global_interval"] + jitter - now,
                    )
                    if wait > 0:
                        time.sleep(wait)
                        now = time.time()
                        waited = wait

                    # 预约式占用时间槽（写完立即生效，防并发/重试绕过）
                    c.execute(
                        "INSERT INTO risk_state(account_id, last_publish_time, publish_count, date, failed_count, last_failure_time) "
                        "VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(account_id) DO UPDATE SET last_publish_time=excluded.last_publish_time",
                        (account_id, now, 0, "", 0, 0),
                    )
                    c.execute(
                        "INSERT INTO risk_state(account_id, last_publish_time, publish_count, date, failed_count, last_failure_time) "
                        "VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(account_id) DO UPDATE SET last_publish_time=excluded.last_publish_time",
                        (GLOBAL_SLOT, now, 0, "", 0, 0),
                    )
            except Exception:
                # 状态读写失败时降级为不等待，不阻塞发布主流程
                return True, 0.0, ""
        return True, waited, ""

    def after_publish(self, account_id, success: bool = True) -> None:
        """发布后调用：成功则当日计数 +1（账号 + 全局槽）；失败则失败计数 +1、记录时间。"""
        account_id = str(account_id)
        try:
            import datetime
            today = datetime.date.today().isoformat()
            now = time.time()
            with _lock:
                with sqlite3.connect(self._db_path, timeout=10) as c:
                    if success:
                        # 账号当日成功计数
                        row = c.execute(
                            "SELECT publish_count, date FROM risk_state WHERE account_id=?", (account_id,)
                        ).fetchone()
                        if row and row[1] == today:
                            c.execute(
                                "UPDATE risk_state SET publish_count=publish_count+1, failed_count=0, last_failure_time=0 "
                                "WHERE account_id=?", (account_id,))
                        else:
                            c.execute(
                                "INSERT INTO risk_state(account_id, last_publish_time, publish_count, date, failed_count, last_failure_time) "
                                "VALUES(?,?,1,?,0,0) ON CONFLICT(account_id) DO UPDATE "
                                "SET publish_count=1, date=excluded.date, failed_count=0, last_failure_time=0",
                                (account_id, now, today))
                        # 全局槽当日成功计数（用于全局每日限额）
                        g = c.execute(
                            "SELECT publish_count, date FROM risk_state WHERE account_id=?", (GLOBAL_SLOT,)
                        ).fetchone()
                        if g and g[1] == today:
                            c.execute(
                                "UPDATE risk_state SET publish_count=publish_count+1 WHERE account_id=?", (GLOBAL_SLOT,))
                        else:
                            c.execute(
                                "INSERT INTO risk_state(account_id, last_publish_time, publish_count, date, failed_count, last_failure_time) "
                                "VALUES(?,?,1,?,0,0) ON CONFLICT(account_id) DO UPDATE "
                                "SET publish_count=1, date=excluded.date",
                                (GLOBAL_SLOT, now, today))
                    else:
                        c.execute(
                            "INSERT INTO risk_state(account_id, last_publish_time, publish_count, date, failed_count, last_failure_time) "
                            "VALUES(?,?,0,'',1,?) ON CONFLICT(account_id) DO UPDATE "
                            "SET failed_count=failed_count+1, last_failure_time=excluded.last_failure_time",
                            (account_id, now, now))
        except Exception:
            pass


# 进程内单例
risk_control = RiskControl()
