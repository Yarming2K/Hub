import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_project_and_task_flow():
    p = client.post("/api/projects", json={"name": "PvP Analyst", "next_step": "Add shield analysis"}).json()
    assert p["open_tasks"] == 0

    t = client.post("/api/tasks", json={"title": "Record shield blocks", "project_id": p["id"], "priority": 2}).json()
    assert t["done"] is False and t["completed_at"] is None

    projects = client.get("/api/projects").json()
    assert next(x for x in projects if x["id"] == p["id"])["open_tasks"] == 1

    done = client.patch(f"/api/tasks/{t['id']}", json={"done": True}).json()
    assert done["done"] is True and done["completed_at"] is not None

    # recently completed tasks still come back; done_days=0 hides them
    assert any(x["id"] == t["id"] for x in client.get("/api/tasks").json())
    assert not any(x["id"] == t["id"] for x in client.get("/api/tasks?done_days=0").json())

    undone = client.patch(f"/api/tasks/{t['id']}", json={"done": False}).json()
    assert undone["completed_at"] is None

    patched = client.patch(f"/api/projects/{p['id']}", json={"status": "paused"}).json()
    assert patched["status"] == "paused"

    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/tasks?project_id={p['id']}").json() == []


def test_validation():
    assert client.post("/api/projects", json={"name": ""}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "priority": 5}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "project_id": 9999}).status_code == 404
    assert client.patch("/api/projects/1", json={"status": "bogus"}).status_code in (404, 422)


def test_token_auth():
    os.environ["APP_TOKEN"] = "s3cret"
    try:
        assert client.get("/api/projects").status_code == 401
        assert client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get("/api/projects", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/api/health").status_code == 200  # health stays open
    finally:
        del os.environ["APP_TOKEN"]


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake\n"


def test_attachments():
    p = client.post("/api/projects", json={"name": "Biology"}).json()
    t = client.post("/api/tasks", json={"title": "Lab report", "project_id": p["id"]}).json()

    a = client.post("/api/attachments", data={"task_id": t["id"]}, files={"file": ("sheet.png", PNG, "image/png")})
    assert a.status_code == 201 and a.json()["content_type"] == "image/png"
    s = client.post("/api/attachments", data={"project_id": p["id"]}, files={"file": ("syllabus.pdf", PDF, "application/pdf")})
    assert s.status_code == 201 and s.json()["content_type"] == "application/pdf"

    # type comes from the bytes, not the claimed type
    bad = client.post("/api/attachments", data={"task_id": t["id"]}, files={"file": ("evil.png", b"<svg onload=alert(1)>", "image/png")})
    assert bad.status_code == 415
    assert client.post("/api/attachments", files={"file": ("x.png", PNG, "image/png")}).status_code == 422
    big = b"%PDF-" + b"0" * (10 * 1024 * 1024)
    assert client.post("/api/attachments", data={"task_id": t["id"]}, files={"file": ("big.pdf", big, "application/pdf")}).status_code == 413

    assert [x["filename"] for x in client.get(f"/api/attachments?task_id={t['id']}").json()] == ["sheet.png"]
    f = client.get(f"/api/attachments/{a.json()['id']}/file")
    assert f.content == PNG and f.headers["content-type"] == "image/png"

    # deleting the task removes its files; deleting the project removes the syllabus
    client.delete(f"/api/tasks/{t['id']}")
    assert client.get(f"/api/attachments/{a.json()['id']}/file").status_code == 404
    client.delete(f"/api/projects/{p['id']}")
    assert client.get(f"/api/attachments?project_id={p['id']}").json() == []
