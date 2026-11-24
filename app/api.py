"""FastAPI 路由层：只做参数校验 + 数据库读写，业务计算尽量放在本文件的小函数里。

接口清单（与 docs/ARCHITECTURE.md 第 6 节一致）：
    GET    /api/health
    GET    /api/today
    POST   /api/clock/in          {plan}
    POST   /api/clock/out         {summary}
    GET    /api/sessions          ?date=YYYY-MM-DD
    GET    /api/screenshots       ?date=YYYY-MM-DD
    GET    /shots/<path>          本地截图文件（不上传云端）
    CRUD   /api/tasks
    CRUD   /api/projects  +  /api/projects/{id}/logs
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import __version__, db as dbm
from .config import Config, load_config

# ================================================================ 依赖
def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """每个请求一个连接；正常返回时自动 commit。"""
    with dbm.connect(request.app.state.db_path) as conn:
        yield conn


# ================================================================ 请求体模型
class _Base(BaseModel):
    # 前端会多传一些字段（比如整个对象回传），这里一律忽略，避免 422
    model_config = ConfigDict(extra="ignore")


def _blank_to_none(value: Any) -> Any:
    """空字符串 / 0 之类的前端占位值统一转成 None。"""
    if value == "" or value == 0:
        return None
    return value


class ClockIn(_Base):
    plan: str | None = Field(default=None, description="今天三件事，换行分隔")

    @field_validator("plan", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ClockOut(_Base):
    summary: str | None = None


class TaskIn(_Base):
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = None
    project_id: int | None = None
    due_date: date_cls | None = None
    due_time: str | None = None
    priority: int = Field(default=2, ge=1, le=3)
    repeat: Literal["none", "daily", "weekdays", "weekly", "monthly"] = "none"
    done: bool = False

    @field_validator("project_id", "due_date", "due_time", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("due_time")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("due_time 需要 HH:MM 格式") from exc
        return value


class TaskPatch(_Base):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = None
    project_id: int | None = None
    due_date: date_cls | None = None
    due_time: str | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    repeat: Literal["none", "daily", "weekdays", "weekly", "monthly"] | None = None
    done: bool | None = None

    @field_validator("project_id", "due_date", "due_time", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("due_time")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("due_time 需要 HH:MM 格式") from exc
        return value


class ProjectIn(_Base):
    name: str = Field(min_length=1, max_length=200)
    status: Literal["active", "paused", "done"] = "active"
    progress: int = Field(default=0, ge=0, le=100)
    deadline: date_cls | None = None
    current_state: str | None = None
    next_step: str | None = None

    @field_validator("deadline", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> Any:
        return _blank_to_none(value)


class ProjectPatch(_Base):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "paused", "done"] | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    deadline: date_cls | None = None
    current_state: str | None = None
    next_step: str | None = None

    @field_validator("deadline", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> Any:
        return _blank_to_none(value)


class ProjectLogIn(_Base):
    text: str = Field(min_length=1)
    date: date_cls | None = None
    minutes: int | None = Field(default=None, ge=0)

    @field_validator("date", "minutes", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> Any:
        return _blank_to_none(value)


# ================================================================ 小工具
def _dump(model: BaseModel) -> dict[str, Any]:
    """取显式传入的字段；日期转成 ISO 字符串，布尔转 0/1 存库。"""
    data = model.model_dump(exclude_unset=True)
    payload: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, date_cls):
            payload[key] = value.isoformat()
        elif isinstance(value, bool):
            payload[key] = int(value)
        else:
            payload[key] = value
    return payload


def _task_out(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["done"] = bool(row.get("done"))
    return row


def _project_out(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _session_minutes(session: dict[str, Any], until: datetime | None = None) -> int:
    """一段打卡的实际时长（分钟）；未下班则算到现在。"""
    start = datetime.fromisoformat(session["clock_in"])
    if session.get("clock_out"):
        end = datetime.fromisoformat(session["clock_out"])
    else:
        end = until or datetime.now()
    return max(0, int((end - start).total_seconds() // 60))


def advance_due(due_date: str | None, repeat: str) -> str | None:
    """按 repeat 规则算出下一次到期日。repeat=none 时原样返回。"""
    if repeat in (None, "none"):
        return due_date
    base = date_cls.fromisoformat(due_date) if due_date else date_cls.today()

    if repeat == "daily":
        nxt = base + timedelta(days=1)
    elif repeat == "weekdays":
        nxt = base + timedelta(days=1)
        while nxt.weekday() >= 5:  # 跳过周六周日
            nxt += timedelta(days=1)
    elif repeat == "weekly":
        nxt = base + timedelta(days=7)
    elif repeat == "monthly":
        year = base.year + (1 if base.month == 12 else 0)
        month = 1 if base.month == 12 else base.month + 1
        day = min(base.day, calendar.monthrange(year, month)[1])  # 31 号进 2 月要收敛
        nxt = date_cls(year, month, day)
    else:
        return due_date
    return nxt.isoformat()


def _require(conn: sqlite3.Connection, table: str, row_id: int, what: str) -> dict[str, Any]:
    row = dbm.get(conn, table, row_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"{what} 不存在（id={row_id}）")
    return row


# ================================================================ 应用工厂
def create_app(config: Config | None = None, db_path: str | Path | None = None) -> FastAPI:
    cfg = config or load_config()
    cfg.ensure_dirs()
    dbp = Path(db_path) if db_path is not None else cfg.db_path
    dbm.init_db(dbp)

    app = FastAPI(
        title="Personal OS",
        version=__version__,
        description="个人工作系统：打卡 / 任务 / 项目 / AI 用量",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.config = cfg
    app.state.db_path = dbp

    # ------------------------------------------------------------ 基础
    @app.get("/api/health", tags=["基础"])
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "db": str(dbp),
            "schema_version": dbm.SCHEMA_VERSION,
            "timezone": cfg.timezone,
        }

    # ------------------------------------------------------------ 打卡
    @app.get("/api/today", tags=["打卡"])
    def today(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """今日汇总：打卡状态 + 任务分组 + 进行中项目 + 今日截图数。"""
        day = dbm.today_str()
        sessions = dbm.all_(
            conn, "SELECT * FROM work_sessions WHERE date = ? ORDER BY clock_in", (day,)
        )
        open_session = next((s for s in sessions if not s.get("clock_out")), None)
        worked = sum(_session_minutes(s) for s in sessions)

        # 任务分组：逾期 / 今天到期 / 高优先级（未在上一组里出现过的）
        open_tasks = [
            _task_out(t)
            for t in dbm.all_(
                conn,
                """SELECT * FROM tasks WHERE done = 0
                   ORDER BY COALESCE(due_date, '9999-12-31'), priority, id""",
            )
        ]
        overdue = [t for t in open_tasks if t.get("due_date") and t["due_date"] < day]
        due_today = [t for t in open_tasks if t.get("due_date") == day]
        listed = {t["id"] for t in overdue} | {t["id"] for t in due_today}
        high = [t for t in open_tasks if t["priority"] == 1 and t["id"] not in listed]
        listed |= {t["id"] for t in high}
        others = [t for t in open_tasks if t["id"] not in listed]
        completed_today = [
            _task_out(t)
            for t in dbm.all_(
                conn,
                "SELECT * FROM tasks WHERE done = 1 AND done_at >= ? ORDER BY done_at DESC",
                (f"{day}T00:00:00",),
            )
        ]

        projects = [
            _project_out(p)
            for p in dbm.all_(
                conn,
                "SELECT * FROM projects WHERE status = 'active' ORDER BY updated_at DESC",
            )
        ]
        shot_count = dbm.one(
            conn, "SELECT COUNT(*) AS n FROM screenshots WHERE taken_at >= ?", (f"{day}T00:00:00",)
        )

        return {
            "date": day,
            "work": {
                "clocked_in": open_session is not None,
                "session": open_session,
                "sessions": sessions,
                "worked_minutes": worked,
            },
            "tasks": {
                "overdue": overdue,
                "due_today": due_today,
                "high": high,
                "others": others,
                "completed_today": completed_today,
                "open_count": len(open_tasks),
            },
            "projects": projects,
            "screenshots": {"count": int((shot_count or {}).get("n", 0))},
        }

    @app.post("/api/clock/in", tags=["打卡"], status_code=201)
    def clock_in(body: ClockIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """上班打卡：创建一段 work_session，记录「今天三件事」。"""
        day = dbm.today_str()
        running = dbm.one(
            conn, "SELECT * FROM work_sessions WHERE date = ? AND clock_out IS NULL", (day,)
        )
        if running:
            raise HTTPException(status_code=409, detail="今天已经上班打卡了，先下班再重新上班")

        session_id = dbm.insert(
            conn,
            "work_sessions",
            {"date": day, "clock_in": dbm.now_iso(), "plan": body.plan},
        )
        return dbm.get(conn, "work_sessions", session_id) or {}

    @app.post("/api/clock/out", tags=["打卡"])
    def clock_out(body: ClockOut, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        """下班打卡：结束当前未关闭的 session，写入总结。"""
        day = dbm.today_str()
        running = dbm.one(
            conn,
            "SELECT * FROM work_sessions WHERE date = ? AND clock_out IS NULL ORDER BY clock_in DESC",
            (day,),
        )
        if not running:
            raise HTTPException(status_code=409, detail="当前没有进行中的打卡，请先上班打卡")

        dbm.update(
            conn,
            "work_sessions",
            running["id"],
            {"clock_out": dbm.now_iso(), "summary": body.summary},
        )
        session = dbm.get(conn, "work_sessions", running["id"]) or {}
        session["worked_minutes"] = _session_minutes(session)
        return session

    @app.get("/api/sessions", tags=["打卡"])
    def list_sessions(
        date: str | None = Query(default=None, description="YYYY-MM-DD，留空返回最近的记录"),
        limit: int = Query(default=100, ge=1, le=500),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> list[dict[str, Any]]:
        if date:
            rows = dbm.all_(
                conn, "SELECT * FROM work_sessions WHERE date = ? ORDER BY clock_in", (date,)
            )
        else:
            rows = dbm.all_(
                conn, "SELECT * FROM work_sessions ORDER BY clock_in DESC LIMIT ?", (limit,)
            )
        for row in rows:
            row["worked_minutes"] = _session_minutes(row)
        return rows

    @app.get("/api/screenshots", tags=["打卡"])
    def list_screenshots(
        date: str | None = Query(default=None),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> list[dict[str, Any]]:
        """截图记录（M1 只读；M2 起由调度器自动写入）。"""
        if date:
            return dbm.all_(
                conn,
                "SELECT * FROM screenshots WHERE taken_at >= ? AND taken_at < ? ORDER BY taken_at",
                (f"{date}T00:00:00", f"{date}T23:59:60"),
            )
        return dbm.all_(
            conn, "SELECT * FROM screenshots ORDER BY taken_at DESC LIMIT 200"
        )

    @app.get("/shots/{rel_path:path}", tags=["打卡"])
    def get_shot(rel_path: str) -> FileResponse:
        """按相对路径返回本地截图；路径必须落在 screenshots/ 内。"""
        base = Path(app.state.config.screenshots_dir).resolve()
        target = (base / rel_path).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            raise HTTPException(status_code=404, detail="截图不存在")
        return FileResponse(target)

    # ------------------------------------------------------------ 任务
    @app.get("/api/tasks", tags=["任务"])
    def list_tasks(
        done: bool | None = Query(default=None),
        project_id: int | None = Query(default=None),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks WHERE 1 = 1"
        params: list[Any] = []
        if done is not None:
            sql += " AND done = ?"
            params.append(int(done))
        if project_id is not None:
            sql += " AND project_id = ?"
            params.append(project_id)
        sql += " ORDER BY done, COALESCE(due_date, '9999-12-31'), priority, id"
        return [_task_out(t) for t in dbm.all_(conn, sql, params)]

    @app.post("/api/tasks", tags=["任务"], status_code=201)
    def create_task(body: TaskIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        payload = _dump(body)
        payload.setdefault("priority", 2)
        payload.setdefault("repeat", "none")
        payload["done"] = int(bool(payload.get("done")))
        if payload.get("done"):
            payload["done_at"] = dbm.now_iso()
        if payload.get("project_id"):
            _require(conn, "projects", payload["project_id"], "项目")
        task_id = dbm.insert(conn, "tasks", payload)
        return _task_out(dbm.get(conn, "tasks", task_id) or {})

    @app.get("/api/tasks/{task_id}", tags=["任务"])
    def get_task(task_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        return _task_out(_require(conn, "tasks", task_id, "任务"))

    @app.patch("/api/tasks/{task_id}", tags=["任务"])
    def update_task(
        task_id: int, body: TaskPatch, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        """局部更新。勾选完成时：普通任务记 done_at；重复任务把到期日推到下一次并复位。"""
        task = _require(conn, "tasks", task_id, "任务")
        payload = _dump(body)
        if not payload:
            return _task_out(task)

        if "project_id" in payload and payload["project_id"]:
            _require(conn, "projects", payload["project_id"], "项目")

        if "done" in payload:
            if payload["done"]:
                repeat = payload.get("repeat", task["repeat"])
                due_date = payload.get("due_date", task["due_date"])
                payload["done_at"] = dbm.now_iso()
                if repeat and repeat != "none":
                    # 重复任务：完成即推迟到下一次，done 立刻复位
                    payload["done"] = 0
                    payload["due_date"] = advance_due(due_date, repeat)
                else:
                    payload["done"] = 1
            else:
                payload["done"] = 0
                payload["done_at"] = None

        dbm.update(conn, "tasks", task_id, payload)
        return _task_out(dbm.get(conn, "tasks", task_id) or {})

    @app.delete("/api/tasks/{task_id}", tags=["任务"], status_code=204)
    def delete_task(task_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> None:
        _require(conn, "tasks", task_id, "任务")
        dbm.delete(conn, "tasks", task_id)

    # ------------------------------------------------------------ 项目
    @app.get("/api/projects", tags=["项目"])
    def list_projects(
        status: str | None = Query(default=None),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> list[dict[str, Any]]:
        if status:
            rows = dbm.all_(
                conn, "SELECT * FROM projects WHERE status = ? ORDER BY updated_at DESC", (status,)
            )
        else:
            rows = dbm.all_(conn, "SELECT * FROM projects ORDER BY status, updated_at DESC")
        # 附带最近一条日志日期，前端用来提示「这个项目停了」
        for row in rows:
            last = dbm.one(
                conn,
                "SELECT MAX(date) AS d FROM project_logs WHERE project_id = ?",
                (row["id"],),
            )
            row["last_log_date"] = (last or {}).get("d")
        return [_project_out(p) for p in rows]

    @app.post("/api/projects", tags=["项目"], status_code=201)
    def create_project(
        body: ProjectIn, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        payload = _dump(body)
        project_id = dbm.insert(conn, "projects", payload)
        return _project_out(dbm.get(conn, "projects", project_id) or {})

    @app.get("/api/projects/{project_id}", tags=["项目"])
    def get_project(
        project_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        project = _project_out(_require(conn, "projects", project_id, "项目"))
        project["logs"] = dbm.all_(
            conn,
            "SELECT * FROM project_logs WHERE project_id = ? ORDER BY date DESC, id DESC LIMIT 30",
            (project_id,),
        )
        project["tasks"] = [
            _task_out(t)
            for t in dbm.all_(
                conn, "SELECT * FROM tasks WHERE project_id = ? ORDER BY done, id", (project_id,)
            )
        ]
        return project

    @app.patch("/api/projects/{project_id}", tags=["项目"])
    def update_project(
        project_id: int, body: ProjectPatch, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        _require(conn, "projects", project_id, "项目")
        payload = _dump(body)
        if payload:
            dbm.update(conn, "projects", project_id, payload)
        return _project_out(dbm.get(conn, "projects", project_id) or {})

    @app.delete("/api/projects/{project_id}", tags=["项目"], status_code=204)
    def delete_project(project_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> None:
        _require(conn, "projects", project_id, "项目")
        dbm.delete(conn, "projects", project_id)

    @app.get("/api/projects/{project_id}/logs", tags=["项目"])
    def list_project_logs(
        project_id: int,
        limit: int = Query(default=50, ge=1, le=500),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> list[dict[str, Any]]:
        _require(conn, "projects", project_id, "项目")
        return dbm.all_(
            conn,
            "SELECT * FROM project_logs WHERE project_id = ? ORDER BY date DESC, id DESC LIMIT ?",
            (project_id, limit),
        )

    @app.post("/api/projects/{project_id}/logs", tags=["项目"], status_code=201)
    def create_project_log(
        project_id: int, body: ProjectLogIn, conn: sqlite3.Connection = Depends(get_conn)
    ) -> dict[str, Any]:
        _require(conn, "projects", project_id, "项目")
        payload = _dump(body)
        payload["project_id"] = project_id
        payload.setdefault("date", dbm.today_str())
        log_id = dbm.insert(conn, "project_logs", payload)
        # 写日志顺便刷新项目的 updated_at，方便「最近没动静」的判断与排序
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?", (dbm.now_iso(), project_id)
        )
        return dbm.get(conn, "project_logs", log_id) or {}

    @app.delete("/api/projects/{project_id}/logs/{log_id}", tags=["项目"], status_code=204)
    def delete_project_log(
        project_id: int, log_id: int, conn: sqlite3.Connection = Depends(get_conn)
    ) -> None:
        _require(conn, "projects", project_id, "项目")
        log = _require(conn, "project_logs", log_id, "项目日志")
        if log["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="日志不属于该项目")
        dbm.delete(conn, "project_logs", log_id)

    # ------------------------------------------------------------ 前端静态文件
    web_dir = Path(__file__).resolve().parent / "web"
    if web_dir.is_dir():
        # 注意：必须最后挂载，否则 /api/* 会被静态文件吃掉
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app
