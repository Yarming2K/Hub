import os
import tempfile
from datetime import date

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import schoology  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

# Shaped like a typical Schoology export: folded lines, UTC times, all-day dates, links in DESCRIPTION
FEED = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Schoology Inc//Schoology//EN\r
X-WR-CALNAME:Schoology\r
BEGIN:VEVENT\r
UID:assignment-111@lms.fcps.edu\r
SUMMARY:Enzyme Lab Report\r
DTSTART:20991021T035900Z\r
DESCRIPTION:Course: Biology 1 - Period 2\\nhttps://lms.fcps.edu/assignment/111\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:event-222@lms.fcps.edu\r
SUMMARY:Unit 3 Vocab Quiz\r
DTSTART;VALUE=DATE:20991022\r
URL:https://lms.fcps.edu/event/222\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:assignment-333@lms.fcps.edu\r
SUMMARY:Ancient history homework\r
DTSTART;VALUE=DATE:20200101\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:assignment-111@lms.fcps.edu\r
SUMMARY:Duplicate of the lab\r
DTSTART;VALUE=DATE:20991021\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:assignment-444@lms.fcps.edu\r
SUMMARY:A very long assignment title that Schoology folds across\r
  two lines in the feed\r
DTSTART;VALUE=DATE:20991030\r
LOCATION:English 10 Honors\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_normalize_url():
    url = schoology.normalize_url("webcal://lms.fcps.edu/calendar/feed/ical/1/abc/ical.ics")
    assert url == "https://lms.fcps.edu/calendar/feed/ical/1/abc/ical.ics"
    assert schoology.normalize_url("https://app.schoology.com/calendar/feed/ical/1/x/ical.ics")
    for bad in ["https://evil.example.com/calendar/x.ics", "http://lms.fcps.edu/calendar/x.ics",
                "https://lms.fcps.edu.evil.com/calendar/x.ics", "https://lms.fcps.edu/user/1", "not a link"]:
        with pytest.raises(schoology.FeedError):
            schoology.normalize_url(bad)


def test_parse_feed():
    events, fields = schoology.parse_feed(FEED)
    by_uid = {}
    for e in events:
        by_uid.setdefault(e["uid"], e)  # first copy wins, like the sync
    lab = by_uid["assignment-111@lms.fcps.edu"]
    # 03:59 UTC on the 21st is 11:59 PM on the 20th in Virginia
    assert lab["due"] == date(2099, 10, 20)
    assert lab["course"] == "Biology 1 - Period 2"
    assert lab["link"] == "https://lms.fcps.edu/assignment/111"
    assert by_uid["event-222@lms.fcps.edu"]["link"] == "https://lms.fcps.edu/event/222"
    assert by_uid["event-222@lms.fcps.edu"]["course"] == ""
    assert by_uid["assignment-444@lms.fcps.edu"]["title"].endswith("across two lines in the feed")
    assert "DTSTART" in fields and "UID" in fields
    with pytest.raises(schoology.FeedError):
        schoology.parse_feed(b"<html>Please log in</html>")


def test_connect_sync_and_resync(monkeypatch):
    assert client.put("/api/schoology", json={"url": "https://evil.example.com/calendar/a.ics"}).status_code == 422
    r = client.put("/api/schoology", json={"url": "webcal://lms.fcps.edu/calendar/feed/ical/1/abc/ical.ics"})
    assert r.json()["connected"] is True

    feed = {"data": FEED}
    monkeypatch.setattr(schoology, "fetch_feed", lambda url: feed["data"])

    res = client.post("/api/schoology/sync").json()
    assert res["added"] == 3 and res["events_in_feed"] == 5  # old one skipped, duplicate ignored
    assert set(res["new_projects"]) == {"Biology 1 - Period 2", "English 10 Honors"}

    tasks = {t["title"]: t for t in client.get("/api/tasks").json() if t["source"] == "schoology"}
    assert tasks["Unit 3 Vocab Quiz"]["project_id"] is None  # no class in the feed, so Inbox
    assert tasks["Enzyme Lab Report"]["link"].endswith("/assignment/111")

    # syncing again changes nothing
    again = client.post("/api/schoology/sync").json()
    assert again["added"] == 0 and again["updated"] == 0

    # you delete one, and the teacher renames another: deleted stays gone, rename comes through
    client.delete(f"/api/tasks/{tasks['Unit 3 Vocab Quiz']['id']}")
    client.patch(f"/api/tasks/{tasks['Enzyme Lab Report']['id']}", json={"done": True, "today": True})
    feed["data"] = FEED.replace(b"SUMMARY:Enzyme Lab Report", b"SUMMARY:Enzyme Lab Report (revised)")
    third = client.post("/api/schoology/sync").json()
    assert third["added"] == 0 and third["updated"] == 1 and third["skipped_deleted"] == 1
    lab = next(t for t in client.get("/api/tasks").json() if t["id"] == tasks["Enzyme Lab Report"]["id"])
    assert lab["title"] == "Enzyme Lab Report (revised)" and lab["done"] is True  # your progress is kept

    assert client.get("/api/schoology").json()["last_sync"]
    assert client.delete("/api/schoology").status_code == 204
    assert client.get("/api/schoology").json()["connected"] is False


def test_sync_errors(monkeypatch):
    client.delete("/api/schoology")
    assert client.post("/api/schoology/sync").status_code == 502  # not connected
    client.put("/api/schoology", json={"url": "https://lms.fcps.edu/calendar/feed/ical/1/abc/ical.ics"})

    def boom(url):
        raise schoology.FeedError("Couldn't reach Schoology. Try again in a minute.")
    monkeypatch.setattr(schoology, "fetch_feed", boom)
    r = client.post("/api/schoology/sync")
    assert r.status_code == 502 and "Schoology" in r.json()["detail"]
