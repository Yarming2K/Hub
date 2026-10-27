# iPhone widget

A home-screen and lock-screen widget that shows all your Hub projects at a glance.

What it shows:
- **Small**: open tasks inside a ring of this week's progress, how many are late or due today, and one bar split by project color.
- **Medium**: totals, plus your top 4 projects, each with a progress ring.
- **Large**: total tiles, the project bar, and up to 6 projects with their ring, open and late counts, and the next thing due (or the project's Next step).
- **Lock screen**: a ring with your open count, or a line with what's late and what's next.

The ring shows done ÷ all tasks for that project, drawn in the project's color. Projects marked done are hidden. Projects with late work come first.

## Set it up (about 3 minutes)

1. Install **Scriptable** (free) from the App Store.
2. Open this file on your phone: `widget/HubWidget.js`. Copy all of it.
3. In Scriptable, tap **+**, paste, and name the script `Hub`.
4. Tap ▶. Paste your Hub address (for example `https://hub-xxxx.onrender.com`) and your `APP_TOKEN`. Both are saved in the iPhone Keychain, not in the script.
5. Go to the home screen, long-press, tap **Edit > Add Widget**, pick **Scriptable**, choose a size, and add it.
6. Long-press the new widget, tap **Edit Widget**, and set **Script** to `Hub`.

Tapping the widget opens your Hub.

## Options

- **Only some projects**: in Edit Widget, set **Parameter** to words from their names, separated by commas, such as `Biology, Chem`.
- **Change the address or token**: run the script in Scriptable and choose **Change Hub address or token**.
- **Preview other sizes**: run the script in Scriptable and pick a size.

## Good to know

- iOS decides when widgets refresh, usually every 15 to 30 minutes.
- The free Render plan sleeps. If the Hub doesn't answer within 20 seconds, the widget shows the last copy it got and says **offline** in the corner.
- The widget only reads. It uses `GET /api/widget`.
