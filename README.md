# Hub

Your projects, tasks and next steps in one place. Phase 1 of 4.

## Run it on your PC

```
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. No token is needed locally.

## Put it online (Render)

1. Push this folder to a new GitHub repo.
2. In Render, choose **New > Blueprint** and pick the repo.
3. When it's live, open the `hub` service, go to **Environment**, and copy `APP_TOKEN`.
4. Open your site and paste the token. Do the same on your phone.

Free tier notes:
- The site sleeps after 15 idle minutes. The first visit after that takes about a minute.
- The free database is deleted after 30 days plus a 14-day grace period. Upgrade it to a paid plan before then to keep your data.

## Using it

- **Today** shows tasks you pinned, plus anything due or overdue. **Coming up** lists the next 7 days.
- **Inbox** holds tasks with no project.
- Each project has one big **Next step**. Click it to edit.
- In the add box, `#pvp` files a task under a project and `!` marks it high priority.
- Press `n` anywhere to jump to the add box.

## Schoology

1. In Schoology, open **Settings** and copy your calendar (iCal) link.
2. In the hub, click **Connect Schoology** in the sidebar and paste it.

What happens:
- Each assignment becomes a task with its due date and an **Open in Schoology** link.
- If the feed names the class, the hub makes a project for it. Otherwise the task goes to **Inbox**, and you can use **Move** to file it.
- It syncs when you open the hub (at most every 30 minutes), or when you click **Sync now**.
- Checking a task off here does not submit anything in Schoology.
- If you delete an imported task, it stays deleted. If a teacher renames an assignment or changes its date, the change comes through.
- Assignments due more than 14 days ago are skipped.

Keep the link private. Anyone with it can see your assignments. If it leaks, reset it in Schoology and paste the new one.

## Sorting the Inbox

- **Sorting rules** (Inbox → Sorting rules): "title contains Bio → Biology". New Schoology assignments are filed automatically. **Sort Inbox now** applies the rules to what's already there.
- **Select to move**: tap several Inbox items and move them to a class in one go.

## Study coach

Each assignment has a **Coach** button. The coach explains, plans, quizzes and gives feedback on your work, but doesn't write answers for you. It can see photos and PDFs attached to that assignment.

To turn it on, add `ANTHROPIC_API_KEY` in Render → the hub service → **Environment**. The key comes from console.anthropic.com; the account must belong to an adult and needs a payment method. Optional settings: `COACH_MODEL` (default `claude-haiku-5-5`) and `COACH_DAILY_LIMIT` (default 80 messages a day).

## Attaching files

- **On a task** (an assignment): hover the task and click **Attach**, or drag a file onto it.
- **On a project** (a class syllabus): use the **Files** section, drag files in, or paste a screenshot.
- Photos and PDFs up to 10 MB. Big phone photos are shrunk automatically.
- On a phone, **Attach** lets you take a photo with the camera.
- Files are stored in the database. The free database holds 1 GB, which is a few thousand photos.

## API

Every `/api/*` route except `/api/health` needs `Authorization: Bearer <APP_TOKEN>`.
Interactive docs are at `/docs`. The desktop companion (phase 3) will use the same API.

## Tests

```
pip install pytest httpx
python -m pytest tests
```

## Roadmap

1. Projects, tasks, next steps, file attachments, Schoology sync, sorting rules, study coach, Blueprint design (done)
2. Focus timer and stats
3. Desktop companion: app tracking and focus overlay
4. AI planner
