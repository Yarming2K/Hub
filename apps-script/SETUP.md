# Hub on Google Apps Script

For school computers. Runs on your school Google account. Nothing to install.

## Set it up (about 5 minutes)

1. Go to **script.google.com** while signed in to your school account. Click **New project**.
2. Click **Untitled project** at the top and rename it **Hub**.
3. **Code.gs**: delete everything in it, then paste all of `Code.gs` from this folder.
4. Click **+** next to **Files**, choose **HTML**, and name it `Index` (Apps Script adds the `.html`).
   Delete what's in it, then paste all of `Index.html` from this folder.
5. Click **Save** (the disk icon).
6. Click **Deploy > New deployment**. Click the gear and choose **Web app**.
   - Execute as: **Me**
   - Who has access: **Only myself**
7. Click **Deploy**, then **Authorize access**. Pick your school account and click **Allow**.
8. Copy the **Web app URL**, open it, and bookmark it.

## Good to know

- Your data is saved in a **Hub data** spreadsheet in your Drive. Attachments go in a **Hub files** folder. Don't delete either one.
- After changing the code later, use **Deploy > Manage deployments**, click the pencil, set **Version: New version**, and click **Deploy**. The URL stays the same.
- If **Authorize access** is blocked, your school may not allow this. Ask your teacher or school IT.
