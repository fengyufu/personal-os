"""api.py：打卡、任务、项目、今日汇总的接口行为。"""

from __future__ import annotations

from datetime import date, timedelta

TODAY = date.today()
TODAY_STR = TODAY.isoformat()


# ---------------------------------------------------------------- 基础
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_web_index_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Personal" in resp.text


# ---------------------------------------------------------------- 打卡
def test_clock_in_and_out(client):
    resp = client.post("/api/clock/in", json={"plan": "1. 写周报\n2. 修 bug"})
    assert resp.status_code == 201
    session = resp.json()
    assert session["plan"].startswith("1. 写周报")
    assert session["clock_out"] is None

    today = client.get("/api/today").json()
    assert today["work"]["clocked_in"] is True
    assert today["work"]["session"]["id"] == session["id"]

    out = client.post("/api/clock/out", json={"summary": "都做完了"})
    assert out.status_code == 200
    assert out.json()["summary"] == "都做完了"
    assert out.json()["worked_minutes"] >= 0

    assert client.get("/api/today").json()["work"]["clocked_in"] is False


def test_clock_in_twice_conflicts(client):
    assert client.post("/api/clock/in", json={"plan": None}).status_code == 201
    resp = client.post("/api/clock/in", json={"plan": None})
    assert resp.status_code == 409
    assert "已经上班" in resp.json()["detail"]


def test_clock_out_without_clock_in_conflicts(client):
    resp = client.post("/api/clock/out", json={"summary": None})
    assert resp.status_code == 409


def test_clock_out_across_midnight(client, db_path):
    """昨晚上班、过了零点再下班：要能正常下班，且今日页显示为上班中。"""
    import sqlite3

    client.post("/api/clock/in", json={"plan": "通宵"})
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE work_sessions SET date = ?, clock_in = ?", (yesterday, f"{yesterday}T22:00:00")
        )

    today = client.get("/api/today").json()
    assert today["work"]["clocked_in"] is True
    assert client.post("/api/clock/in", json={}).status_code == 409

    out = client.post("/api/clock/out", json={"summary": "收工"})
    assert out.status_code == 200
    assert out.json()["worked_minutes"] >= 120

    # 下班后今日页仍然认得这段：状态是「已下班」，计划还在，工时只算零点之后
    after = client.get("/api/today").json()
    assert after["work"]["clocked_in"] is False
    assert [s["plan"] for s in after["work"]["sessions"]] == ["通宵"]
    from datetime import datetime
    since_midnight = (datetime.now() - datetime.combine(TODAY, datetime.min.time())).total_seconds() // 60
    assert 0 <= after["work"]["worked_minutes"] <= since_midnight + 1


def test_shots_rejects_sibling_dir(client, config):
    """screenshots_evil/ 这类前缀相同的目录不能被读到。"""
    evil = config.screenshots_dir.parent / (config.screenshots_dir.name + "_evil")
    evil.mkdir(parents=True)
    (evil / "x.png").write_bytes(b"x")
    config.screenshots_dir.mkdir(parents=True, exist_ok=True)
    # 用 %2e%2e 编码，避免 HTTP 客户端提前把 ../ 规范化掉，确保真的打到 /shots 路由
    resp = client.get(f"/shots/%2e%2e/{evil.name}/x.png")
    assert resp.status_code == 404


def test_sessions_by_date(client):
    client.post("/api/clock/in", json={"plan": "a"})
    client.post("/api/clock/out", json={"summary": "b"})
    rows = client.get(f"/api/sessions?date={TODAY_STR}").json()
    assert len(rows) == 1
    assert rows[0]["worked_minutes"] >= 0


def test_screenshots_empty(client):
    assert client.get(f"/api/screenshots?date={TODAY_STR}").json() == []


def test_shots_path_traversal_blocked(client):
    resp = client.get("/shots/../../config.toml")
    assert resp.status_code == 404


# ---------------------------------------------------------------- 任务
def test_task_crud(client):
    created = client.post("/api/tasks", json={"title": "买菜", "priority": 1})
    assert created.status_code == 201
    task = created.json()
    assert task["done"] is False
    assert task["repeat"] == "none"

    fetched = client.get(f"/api/tasks/{task['id']}").json()
    assert fetched["title"] == "买菜"

    patched = client.patch(f"/api/tasks/{task['id']}", json={"title": "买菜 + 做饭", "done": True})
    assert patched.json()["title"] == "买菜 + 做饭"
    assert patched.json()["done"] is True
    assert patched.json()["done_at"]

    assert client.get("/api/tasks?done=true").json()[0]["id"] == task["id"]
    assert client.get("/api/tasks?done=false").json() == []

    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_task_validation(client):
    assert client.post("/api/tasks", json={"title": ""}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "priority": 9}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "due_time": "25:00"}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "repeat": "yearly"}).status_code == 422


def test_task_empty_strings_become_null(client):
    task = client.post(
        "/api/tasks", json={"title": "x", "due_date": "", "due_time": "", "project_id": ""}
    ).json()
    assert task["due_date"] is None and task["due_time"] is None and task["project_id"] is None


def test_repeating_task_advances_instead_of_completing(client):
    """重复任务勾完就推到下一次，done 立刻复位。"""
    task = client.post(
        "/api/tasks",
        json={"title": "站会", "due_date": TODAY_STR, "repeat": "daily"},
    ).json()

    after = client.patch(f"/api/tasks/{task['id']}", json={"done": True}).json()
    assert after["done"] is False
    assert after["due_date"] == (TODAY + timedelta(days=1)).isoformat()
    assert after["done_at"]

    # 再勾一次继续往后推
    again = client.patch(f"/api/tasks/{task['id']}", json={"done": True}).json()
    assert again["due_date"] == (TODAY + timedelta(days=2)).isoformat()


def test_task_unknown_project_rejected(client):
    resp = client.post("/api/tasks", json={"title": "x", "project_id": 999})
    assert resp.status_code == 404


# ---------------------------------------------------------------- 项目
def test_project_crud_and_logs(client):
    project = client.post(
        "/api/projects", json={"name": "Personal OS", "next_step": "做 M2"}
    ).json()
    assert project["status"] == "active"
    assert project["progress"] == 0

    updated = client.patch(
        f"/api/projects/{project['id']}", json={"progress": 45, "current_state": "M1 完成"}
    ).json()
    assert updated["progress"] == 45

    log = client.post(
        f"/api/projects/{project['id']}/logs", json={"text": "写完 db.py", "minutes": 90}
    )
    assert log.status_code == 201
    assert log.json()["date"] == TODAY_STR

    detail = client.get(f"/api/projects/{project['id']}").json()
    assert len(detail["logs"]) == 1
    assert detail["logs"][0]["text"] == "写完 db.py"

    listed = client.get("/api/projects").json()
    assert listed[0]["last_log_date"] == TODAY_STR

    assert client.delete(f"/api/projects/{project['id']}/logs/{log.json()['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}/logs").json() == []

    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404


def test_project_cascade_deletes_logs(client):
    project = client.post("/api/projects", json={"name": "临时"}).json()
    client.post(f"/api/projects/{project['id']}/logs", json={"text": "一条日志"})
    client.delete(f"/api/projects/{project['id']}")
    assert client.get(f"/api/projects/{project['id']}/logs").status_code == 404


def test_project_status_filter(client):
    a = client.post("/api/projects", json={"name": "进行中"}).json()
    b = client.post("/api/projects", json={"name": "暂停"}).json()
    client.patch(f"/api/projects/{b['id']}", json={"status": "paused"})

    active = client.get("/api/projects?status=active").json()
    assert [p["id"] for p in active] == [a["id"]]


def test_log_project_mismatch_404(client):
    a = client.post("/api/projects", json={"name": "A"}).json()
    b = client.post("/api/projects", json={"name": "B"}).json()
    log = client.post(f"/api/projects/{a['id']}/logs", json={"text": "x"}).json()
    assert client.delete(f"/api/projects/{b['id']}/logs/{log['id']}").status_code == 404


# ---------------------------------------------------------------- 今日汇总
def test_today_groups_tasks(client):
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    tomorrow = (TODAY + timedelta(days=1)).isoformat()

    overdue = client.post("/api/tasks", json={"title": "逾期", "due_date": yesterday}).json()
    due_today = client.post("/api/tasks", json={"title": "今天", "due_date": TODAY_STR}).json()
    high = client.post("/api/tasks", json={"title": "高优先", "priority": 1}).json()
    later = client.post(
        "/api/tasks", json={"title": "以后", "due_date": tomorrow, "priority": 3}
    ).json()

    data = client.get("/api/today").json()
    assert [t["id"] for t in data["tasks"]["overdue"]] == [overdue["id"]]
    assert [t["id"] for t in data["tasks"]["due_today"]] == [due_today["id"]]
    assert [t["id"] for t in data["tasks"]["high"]] == [high["id"]]
    assert [t["id"] for t in data["tasks"]["others"]] == [later["id"]]
    assert data["tasks"]["open_count"] == 4

    client.patch(f"/api/tasks/{due_today['id']}", json={"done": True})
    data = client.get("/api/today").json()
    assert [t["id"] for t in data["tasks"]["completed_today"]] == [due_today["id"]]
    assert data["tasks"]["open_count"] == 3


def test_today_shows_active_projects_only(client):
    active = client.post("/api/projects", json={"name": "在做的"}).json()
    paused = client.post("/api/projects", json={"name": "暂停的"}).json()
    client.patch(f"/api/projects/{paused['id']}", json={"status": "paused"})
    data = client.get("/api/today").json()
    assert [p["id"] for p in data["projects"]] == [active["id"]]


# ---------------------------------------------------------------- 日历
def test_calendar_expands_repeat_and_deadlines(client):
    start = TODAY
    client.post("/api/tasks", json={"title": "日报", "due_date": TODAY_STR, "repeat": "daily"})
    client.post("/api/tasks", json={"title": "一次性", "due_date": TODAY_STR})
    client.post("/api/projects", json={"name": "P", "deadline": (TODAY + timedelta(days=3)).isoformat()})
    client.post("/api/clock/in", json={})

    end = start + timedelta(days=6)
    data = client.get(f"/api/calendar?start={start}&end={end}").json()
    days = data["days"]

    first = {t["title"]: t for t in days[TODAY_STR]["tasks"]}
    assert set(first) == {"日报", "一次性"}
    assert first["日报"]["virtual"] is False
    later = days[(TODAY + timedelta(days=2)).isoformat()]["tasks"]
    assert [t["title"] for t in later] == ["日报"] and later[0]["virtual"] is True
    assert days[(TODAY + timedelta(days=3)).isoformat()]["deadlines"][0]["name"] == "P"
    assert TODAY_STR in days and days[TODAY_STR]["minutes"] >= 0


def test_calendar_rejects_bad_range(client):
    assert client.get(f"/api/calendar?start={TODAY}&end={TODAY - timedelta(days=1)}").status_code == 400
    assert client.get(f"/api/calendar?start={TODAY}&end={TODAY + timedelta(days=90)}").status_code == 400


def test_parallel_requests_no_thread_error(client):
    """并发请求时 yield 依赖跨线程 commit 不能报错（M1 时前端项目页会 500）。"""
    from concurrent.futures import ThreadPoolExecutor

    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: client.get(f"/api/projects/{pid}/logs").status_code, range(40)))
    assert set(codes) == {200}
