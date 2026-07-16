# Gmail filter automation

Creates the 6 standing Gmail filters below and backfills the same
label/archive/star action onto matching mail already sitting in your inbox.

| # | Name | Action |
|---|------|--------|
| 1 | Promotions | skip inbox, archive, label "Promotions" |
| 2 | Priority | star, label "Priority" (stays in inbox) |
| 3 | Faxes | skip inbox, archive, label "Faxes" |
| 4 | AP Records | skip inbox, archive, label "AP Records" |
| 5 | System Logs | skip inbox, archive, label "System Logs" |
| 6 | Prospect Forwards | star, label "Prospect Forwards" (stays in inbox) |

The exact search queries live in `setup_filters.py` (`FILTERS` list) — read
them there before running anything.

**Note on overlap:** filters 1/3/4/5 (archive) and 2/6 (star, stay in inbox)
are independent Gmail filters, not mutually exclusive rules. A message can
match more than one — e.g. something from `mcmap.chase.com` matches
Promotions and, if it doesn't hit one of Priority's `-from:`/`-category:`
exclusions, could also match Priority. When both match, the archive action
wins for inbox placement (the message leaves the inbox) but it also picks up
the Priority label and star. That mirrors what would happen if you set these
up by hand in Gmail's filter UI with the same search strings.

## 1. One-time Google Cloud / OAuth setup

You're on Google Workspace, so you have an extra option (Internal consent
screen) that personal Gmail accounts don't get — it skips Google's
app-verification review entirely. Use it if you can.

1. **Create/select a project.** Go to
   [console.cloud.google.com](https://console.cloud.google.com), signed in as
   your `b.halpern@cardinal-building.com` account. Click the project
   dropdown (top left) → **New Project**. Name it something like
   `gmail-filter-automation`, leave the org/location as your Workspace
   domain, and create it.
   - If project creation is greyed out or blocked, your Workspace admin has
     restricted who can create Cloud projects — ask them to create one for
     you or grant you the `roles/resourcemanager.projectCreator` role.

2. **Enable the Gmail API.** In the left sidebar: **APIs & Services** →
   **Library** → search "Gmail API" → **Enable**.

3. **Configure the OAuth consent screen.** **APIs & Services** → **OAuth
   consent screen**.
   - User type: choose **Internal** if it's offered (it will be, since this
     is a Workspace account) — this restricts the app to users in your
     `cardinal-building.com` org and requires no Google review, and refresh
     tokens don't expire on a timer. If you only see **External**, pick
     that, keep the app in **Testing** status, and add your own email under
     **Test users** (fine for personal use, but Google expires refresh
     tokens for unverified External-testing apps after 7 days, so you'd need
     to redo the `token.json` auth step weekly).
   - Fill in app name (e.g. "Gmail Filter Automation"), your email as user
     support email and developer contact.
   - Scopes screen: click **Add or Remove Scopes** and add these three
     (search by the full URL if they don't show in the picker):
     - `https://www.googleapis.com/auth/gmail.settings.basic`
     - `https://www.googleapis.com/auth/gmail.modify`
     - `https://www.googleapis.com/auth/gmail.labels`
   - Save through the remaining steps (Internal apps skip the test-user /
     verification screens).

4. **Create an OAuth client ID.** **APIs & Services** → **Credentials** →
   **Create Credentials** → **OAuth client ID**.
   - Application type: **Desktop app**.
   - Name it e.g. "Gmail Filter Script".
   - Click **Create**, then **Download JSON**. Save it as
     `credentials.json` in this `gmail_filters/` folder. This file is in
     `.gitignore` — never commit it.

5. **If your Workspace admin restricts third-party/unconfigured app
   access** (Admin console → Security → API Controls → App access
   control), you may need to add this OAuth client as a **Trusted app**
   even with an Internal consent screen. If the first auth attempt (step 3
   below) errors out with something like "this app is blocked," that's the
   likely cause — ask your admin to allowlist it.

## 2. Install dependencies

```bash
cd gmail_filters
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## 3. Review the script

Read through `setup_filters.py`, in particular the `FILTERS` list and the
`build_action` / `backfill_filter` functions, before running anything. It
makes no account changes just by being imported — nothing runs until you
execute one of the commands below.

## 4. Run it

```bash
# Preview only -- lists what would be created/labeled, changes nothing
python setup_filters.py --dry-run

# Create the 6 filters and backfill matching inbox mail (asks to confirm first)
python setup_filters.py

# Same, but skip the confirmation prompt
python setup_filters.py --yes
```

The first real run opens a browser window for the Google OAuth consent
flow and then stores a refresh token in `token.json` (also gitignored) so
you won't have to re-auth on later runs.

Useful flags:
- `--skip-backfill` — only create the standing filters, don't touch
  existing mail.
- `--skip-filters` — only backfill existing mail, don't create/check
  filters (e.g. if you already created them by hand).
- `--credentials PATH` / `--token PATH` — override the default file
  locations.

The script is idempotent: re-running it won't create duplicate labels, and
it skips creating a filter if one with the exact same search query already
exists. Backfill re-scans `in:inbox <query>` each time, so re-running just
re-applies to whatever new matching mail has landed in the inbox since.
