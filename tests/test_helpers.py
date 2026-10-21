import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import coach, schoology  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake\n"


def make_project(name):
    return client.post("/api/projects", json={"name": name}).json()


def test_bulk_move():
    bio = make_project("Bio bulk")
    a = client.post("/api/tasks", json={"title": "one"}).json()
    b = client.post("/api/tasks", json={"title": "two"}).json()
    r = client.post("/api/tasks/move", json={"ids": [a["id"], b["id"]], "project_id": bio["id"]})
    assert r.json() == {"moved": 2}
    assert {t["project_id"] for t in client.get(f"/api/tasks?project_id={bio['id']}").json()} == {bio["id"]}
    assert client.post("/api/tasks/move", json={"ids": [a["id"]], "project_id": 99999}).status_code == 404
    assert client.post("/api/tasks/move", json={"ids": [], "project_id": None}).status_code == 422
    # back to Inbox
    assert client.post("/api/tasks/move", json={"ids": [a["id"]], "project_id": None}).json() == {"moved": 1}


def test_rules_apply_and_cleanup():
    bio = make_project("Biology rules")
    apbio = make_project("AP Bio rules")
    eng = make_project("English rules")
    assert client.post("/api/rules", json={"keyword": "  ", "project_id": bio["id"]}).status_code == 422
    assert client.post("/api/rules", json={"keyword": "x", "project_id": 99999}).status_code == 404
    r1 = client.post("/api/rules", json={"keyword": "bio", "project_id": bio["id"]}).json()
    client.post("/api/rules", json={"keyword": "AP Bio", "project_id": apbio["id"]})
    client.post("/api/rules", json={"keyword": "essay", "project_id": eng["id"]})
    t1 = client.post("/api/tasks", json={"title": "Bio: organelles diagram"}).json()
    t2 = client.post("/api/tasks", json={"title": "AP Bio lab"}).json()
    t3 = client.post("/api/tasks", json={"title": "Persuasive Essay draft"}).json()
    t4 = client.post("/api/tasks", json={"title": "Algebra worksheet"}).json()
    moved = client.post("/api/rules/apply").json()["moved"]
    assert moved >= 3
    by_id = {t["id"]: t for t in client.get("/api/tasks").json()}
    assert by_id[t1["id"]]["project_id"] == bio["id"]
    assert by_id[t2["id"]]["project_id"] == apbio["id"]  # longer keyword wins
    assert by_id[t3["id"]]["project_id"] == eng["id"]  # case-insensitive
    assert by_id[t4["id"]]["project_id"] is None
    client.delete(f"/api/rules/{r1['id']}")
    assert all(r["id"] != r1["id"] for r in client.get("/api/rules").json())
    client.delete(f"/api/projects/{eng['id']}")  # its rule goes with it
    assert all(r["project_id"] != eng["id"] for r in client.get("/api/rules").json())


FEED = b"""BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:rule-1@lms.fcps.edu\r
SUMMARY:Chem lab safety quiz\r
DTSTART;VALUE=DATE:20991022\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:rule-2@lms.fcps.edu\r
SUMMARY:Geometry proofs\r
DTSTART;VALUE=DATE:20991023\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_sync_uses_rules(monkeypatch):
    chem = make_project("Chemistry")
    client.post("/api/rules", json={"keyword": "chem", "project_id": chem["id"]})
    client.put("/api/schoology", json={"url": "https://lms.fcps.edu/calendar/feed/ical/1/x/ical.ics"})
    monkeypatch.setattr(schoology, "fetch_feed", lambda url: FEED)
    r = client.post("/api/schoology/sync").json()
    assert r["added"] == 2 and r["sorted"] == 1
    tasks = {t["title"]: t for t in client.get("/api/tasks").json()}
    assert tasks["Chem lab safety quiz"]["project_id"] == chem["id"]
    assert tasks["Geometry proofs"]["project_id"] is None
    client.delete("/api/schoology")


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def coach_api(monkeypatch):
    calls = []

    def fake_post(url, json, timeout, headers):
        calls.append({"url": url, "json": json, "headers": headers})
        return fake_post.response

    fake_post.response = FakeResponse(200, {"content": [{"type": "text", "text": "Start with **step 1**."}]})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(httpx, "post", fake_post)
    return fake_post, calls


def test_coach_off_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/api/coach").json()["enabled"] is False
    t = client.post("/api/tasks", json={"title": "no key"}).json()
    r = client.post(f"/api/tasks/{t['id']}/coach", json={"message": "help"})
    assert r.status_code == 502 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert client.get(f"/api/tasks/{t['id']}/coach").json() == []


def test_coach_conversation(coach_api):
    fake_post, calls = coach_api
    assert client.get("/api/coach").json()["enabled"] is True
    p = make_project("Biology coach")
    t = client.post("/api/tasks", json={"title": "Enzyme lab", "project_id": p["id"], "due": "2099-10-21"}).json()
    client.post("/api/attachments", data={"task_id": t["id"]}, files={"file": ("sheet.png", PNG, "image/png")})
    client.post("/api/attachments", data={"task_id": t["id"]}, files={"file": ("rubric.pdf", PDF, "application/pdf")})

    r = client.post(f"/api/tasks/{t['id']}/coach", json={"message": "Break it into steps"})
    assert r.status_code == 200 and r.json() == {"role": "assistant", "content": "Start with **step 1**."}
    req = calls[-1]["json"]
    assert calls[-1]["headers"]["x-api-key"] == "sk-test"
    assert req["model"] == coach.MODEL and "Enzyme lab" in req["system"] and "Biology coach" in req["system"]
    assert "Don't produce work meant to be handed in" in req["system"]
    first = req["messages"][0]["content"]
    assert [b["type"] for b in first] == ["image", "document", "text", "text"]
    assert first[0]["source"]["media_type"] == "image/png"
    assert first[-1]["text"] == "Break it into steps"

    fake_post.response = FakeResponse(200, {"content": [{"type": "text", "text": "Good. Now step 2."}]})
    client.post(f"/api/tasks/{t['id']}/coach", json={"message": "done with step 1"})
    roles = [m["role"] for m in calls[-1]["json"]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert [m["role"] for m in client.get(f"/api/tasks/{t['id']}/coach").json()] == ["user", "assistant"] * 2

    # failures don't save the question, and say what to do
    fake_post.response = FakeResponse(400, {"error": {"message": "Your credit balance is too low"}})
    r = client.post(f"/api/tasks/{t['id']}/coach", json={"message": "again"})
    assert r.status_code == 502 and "out of credit" in r.json()["detail"]
    fake_post.response = FakeResponse(401, {"error": {"message": "invalid x-api-key"}})
    assert "rejected" in client.post(f"/api/tasks/{t['id']}/coach", json={"message": "again"}).json()["detail"]
    assert len(client.get(f"/api/tasks/{t['id']}/coach").json()) == 4

    assert client.delete(f"/api/tasks/{t['id']}/coach").status_code == 204
    assert client.get(f"/api/tasks/{t['id']}/coach").json() == []
    assert client.post(f"/api/tasks/{t['id']}/coach", json={"message": "   "}).status_code == 422


def test_coach_daily_limit(coach_api, monkeypatch):
    from app.db import SessionLocal
    with SessionLocal() as db:
        used = coach._count_today(db, bump=False)
    monkeypatch.setattr(coach, "DAILY_LIMIT", used + 1)
    t = client.post("/api/tasks", json={"title": "limit"}).json()
    assert client.post(f"/api/tasks/{t['id']}/coach", json={"message": "a"}).status_code == 200
    r = client.post(f"/api/tasks/{t['id']}/coach", json={"message": "b"})
    assert r.status_code == 502 and "daily limit" in r.json()["detail"]


def test_deleting_task_removes_coach_history(coach_api):
    t = client.post("/api/tasks", json={"title": "temp"}).json()
    client.post(f"/api/tasks/{t['id']}/coach", json={"message": "hi"})
    assert client.delete(f"/api/tasks/{t['id']}").status_code == 204
    assert client.get(f"/api/tasks/{t['id']}/coach").status_code == 404
