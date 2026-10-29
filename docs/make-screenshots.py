#!/usr/bin/env python3
"""Render README screenshots of the search overlay from a made-up demo index, never from real sessions.

The page is bin/session-search.html as shipped; this script only feeds it demo data, a stub of agterm's page
bridge, a terminal theme, and a query. Needs Google Chrome.

usage: docs/make-screenshots.py
"""

import json
import os
import subprocess
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs")
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
NOW = time.time()
HOUR, DAY = 3600, 86400

# a dark theme in the shape agterm hands every page; the page reads these and never declares them
THEME = """:root { --agterm-background: #1e1e2e; --agterm-foreground: #cdd6f4; --agterm-color-1: #f38ba8;
  --agterm-color-2: #a6e3a1; --agterm-color-3: #f9e2af; --agterm-color-4: #89b4fa; }"""


def conv(title, summary, text, cwd, age=0, exact=True):
    return {"id": f"demo-{abs(hash(title)) % 10**8}", "title": title, "summary": summary, "text": text,
            "mtime": NOW - age, "exact": exact, "cwd": cwd}


def open_session(sid, name, cwd, workspace, convs, scroll="", status="", context="", active=False):
    return {"kind": "open", "id": sid, "name": name, "cwd": cwd, "context": context, "status": status,
            "active": active, "realized": True, "workspace": workspace, "window": "W1", "windowName": "window 1",
            "windowActive": True, "claude": convs, "demoScroll": scroll}


SESSIONS = [
    open_session(
        "S1", "🧪 Flaky login test investigation", "/Users/demo/src/shop-web", "web",
        [conv("Flaky login test investigation",
              "Tracks down why the login end-to-end test fails about one run in ten on CI. The test read the session "
              "cookie before the redirect set it; the fix waits for the redirect and is open as PR #412.",
              "USER: the login e2e test is flaky on CI again\nCLAUDE: It reads the cookie before the redirect "
              "finishes. Waiting on the navigation fixes it locally across 200 runs.", "/Users/demo/src/shop-web")],
        scroll="$ npm run test:e2e -- login\n  ✓ shows the login form\n  ✓ logs in with valid credentials (2.1s)\n"
               "  12 passing", status="completed"),
    open_session(
        "S2", "🗄️ Orders table migration", "/Users/demo/src/shop-api", "api",
        [conv("Orders table migration",
              "Splits the orders table so line items live in their own table, with a backfill that runs in batches "
              "of 5,000 rows to keep replication lag low. The migration is written and tested on a copy of "
              "production data; the backfill job still needs a dry run.",
              "USER: write the migration to move line items out of orders\nCLAUDE: Added 0042_split_line_items "
              "with a batched backfill.", "/Users/demo/src/shop-api")],
        scroll="$ make migrate-dry-run\nApplying 0042_split_line_items... OK (dry run)\n"
               "Backfill: 1,284,113 rows in 257 batches, est. 6m", status="active",
        context="Line-items split, ready for the backfill dry run"),
    open_session(
        "S3", "🚦 Rate limiter redesign", "/Users/demo/src/shop-api", "api",
        [conv("Rate limiter redesign",
              "Replaces the fixed-window rate limiter with a sliding-window one backed by Redis, so bursts at the "
              "window edge no longer double the allowed rate. Design agreed; implementation is half done.",
              "USER: why do we let through twice the limit at minute boundaries?\nCLAUDE: The fixed window resets "
              "at :00, so a burst at :59 and another at :00 both pass.", "/Users/demo/src/shop-api")],
        status="blocked"),
    open_session(
        "S4", "🔐 Ingress TLS certificate renewal", "/Users/demo/src/infra", "infra",
        [conv("Ingress TLS certificate renewal",
              "Finds why the staging ingress served an expired certificate: the issuer's DNS challenge lacked "
              "permission on the new zone. Permission added and the certificate renewed.",
              "USER: staging serves an expired cert\nCLAUDE: cert-manager's DNS-01 challenge is failing with "
              "AccessDenied on the new hosted zone.", "/Users/demo/src/infra")]),
    open_session(
        "S5", "k9s", "/Users/demo/src/infra", "infra", [],
        scroll="Pods(shop)[12]  NAME                     READY  STATUS   RESTARTS\n"
               "api-7c9d5b6f4-2xk8p      1/1    Running  0\nworker-migration-backfill  1/1    Running  0"),
    open_session(
        "S6", "🌗 Dark mode toggle in settings", "/Users/demo/src/shop-web", "web",
        [conv("Dark mode toggle in settings",
              "Adds a light/dark/system theme switch to the settings page, stored per user and applied before first "
              "paint to avoid a flash.", "USER: add a dark mode toggle to settings", "/Users/demo/src/shop-web")],
        active=True),
    {"kind": "recent", "id": "R1", "sessionIDs": ["S7"], "name": "🐘 Postgres 17 upgrade rehearsal",
     "cwd": "/Users/demo/src/infra", "context": "", "status": "", "workspace": "infra", "closedAt": NOW - 3 * HOUR,
     "claude": [conv("Postgres 17 upgrade rehearsal",
                     "Rehearses the major upgrade on a snapshot: extension versions checked, logical replication "
                     "slots dropped first, and every pending schema migration applied before the switchover.",
                     "USER: rehearse the postgres upgrade on the snapshot", "/Users/demo/src/infra", 3 * HOUR)]},
    {"kind": "recent", "id": "R2", "sessionIDs": ["S8"], "name": "⚛️ React 19 upgrade", "cwd": "/Users/demo/src/shop-web",
     "context": "", "status": "", "workspace": "web", "closedAt": NOW - DAY,
     "claude": [conv("React 19 upgrade", "Upgrades the storefront to React 19 and replaces two deprecated APIs.",
                     "USER: upgrade react", "/Users/demo/src/shop-web", DAY)]},
    {"kind": "conversation", "id": "C1", "name": "Users email backfill migration", "cwd": "/Users/demo/src/shop-api",
     "context": "", "status": "", "workspace": "", "closedAt": NOW - 6 * DAY,
     "claude": [conv("Users email backfill migration",
                     "Normalizes stored emails to lowercase with a migration and a backfill, after duplicate accounts "
                     "were created with differently cased addresses.",
                     "USER: we have duplicate users with different email casing", "/Users/demo/src/shop-api", 6 * DAY)]},
    {"kind": "conversation", "id": "C2", "name": "Payment webhook retries", "cwd": "/Users/demo/src/shop-api",
     "context": "", "status": "", "workspace": "", "closedAt": NOW - 12 * DAY,
     "claude": [conv("Payment webhook retries",
                     "Makes the payment webhook handler idempotent so provider retries no longer create duplicate "
                     "refunds.", "USER: refunds are duplicated when the webhook retries", "/Users/demo/src/shop-api",
                     12 * DAY)]},
]

BRIDGE = """<script>
window.agterm = { request(cmd, opts) {
  if (cmd === 'session.text') return Promise.resolve({text: SCROLL[opts.target] || ''});
  return Promise.resolve({});
}};
</script>"""


def render(name, query, height):
    page = open(os.path.join(ROOT, "bin", "session-search.html")).read()
    scroll = {s["id"]: s.get("demoScroll", "") for s in SESSIONS}
    stub = f"<style>{THEME}</style><script>const SCROLL = {json.dumps(scroll)};</script>{BRIDGE}"
    driver = f"""<script type="module">
setTimeout(() => {{ const q = document.getElementById('q'); q.value = {json.dumps(query)};
  q.dispatchEvent(new Event('input')); }}, 300);
</script></body>"""
    page = (page.replace("/*SESSIONS*/[]", json.dumps(SESSIONS).replace("<", "\\u003c"), 1)
                .replace("<style>", stub + "<style>", 1)
                .replace("</body>", driver, 1))
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(page)
    out = os.path.join(OUT, name)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--window-size=1100,{height}", "--virtual-time-budget=2000", f"--screenshot={out}",
                    "file://" + f.name], check=True, capture_output=True)
    os.unlink(f.name)
    print(out)


if __name__ == "__main__":
    render("search-query.png", "migration", 392)
    render("search-all.png", "", 650)
