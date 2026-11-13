"""Generate profile charts from public GitHub calendar and REST search data."""

import collections
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

USER = "zhongxinxuhk"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "stats"
COLORS = ["#b4a5f5", "#82d9f2", "#f4acd1", "#d2c5fc", "#9eb6ec"]
BG = "#16182c"
TEXT = "#f0edff"
MUTED = "#b0b5cf"


def fetch(url, as_json=True):
    headers = {"User-Agent": "GitHub-Profile-Analytics", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=45) as response:
                content = response.read().decode("utf-8")
            return json.loads(content) if as_json else content
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = {}
        self.counts = {}
        self.tooltip_id = None
        self.tooltip_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "data-date" in attrs:
            self.cells[attrs["id"]] = attrs["data-date"]
        if tag == "tool-tip" and attrs.get("for") in self.cells:
            self.tooltip_id = attrs["for"]
            self.tooltip_text = []

    def handle_data(self, data):
        if self.tooltip_id:
            self.tooltip_text.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.tooltip_id:
            text = "".join(self.tooltip_text).strip()
            if text.startswith("No contributions"):
                count = 0
            else:
                match = re.match(r"([\d,]+) contribution", text)
                if not match:
                    raise ValueError(f"Unexpected calendar count: {text}")
                count = int(match.group(1).replace(",", ""))
            self.counts[self.cells[self.tooltip_id]] = count
            self.tooltip_id = None


def search(endpoint, query, per_page=1, page=1):
    params = urllib.parse.urlencode({"q": query, "per_page": per_page, "page": page})
    result = fetch(f"https://api.github.com/search/{endpoint}?{params}")
    if result.get("incomplete_results"):
        raise ValueError("GitHub returned incomplete search results")
    return result


def collect():
    parser = CalendarParser()
    parser.feed(fetch(f"https://github.com/users/{USER}/contributions", as_json=False))
    if len(parser.counts) < 365 or len(parser.counts) != len(parser.cells):
        raise ValueError("Contribution calendar is missing dates or counts")
    # GitHub returns complete calendar weeks. Keep the latest 365 returned days.
    days = dict(sorted(parser.counts.items())[-365:])
    start, end = min(days), max(days)
    period = f"{start}..{end}"
    commits_query = f"author:{USER} committer-date:{period} is:public"
    commits = search("commits", commits_query, per_page=100)
    total_commits = commits["total_count"]
    items = list(commits["items"])
    # A bounded, explicitly labelled sample if the search exceeds GitHub's 1,000-result cap.
    for page in range(2, min((total_commits + 99) // 100, 10) + 1):
        time.sleep(2)
        items.extend(search("commits", commits_query, per_page=100, page=page)["items"])
    unique = {(item["repository"]["full_name"], item["sha"]) for item in items}
    activity = {"Commits": total_commits}
    for label, kind in [("Pull requests", "pr"), ("Issues", "issue")]:
        query = f"author:{USER} type:{kind} created:{period} is:public"
        activity[label] = search("issues", query)["total_count"]
    repositories = []
    page = 1
    while True:
        batch = fetch(f"https://api.github.com/users/{USER}/repos?type=owner&per_page=100&page={page}")
        repositories.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    owned = [repo for repo in repositories if not repo["fork"] and not repo["archived"]]
    languages = collections.Counter(repo["language"] for repo in owned if repo["language"])
    active = collections.Counter(repository for repository, sha in unique)
    return {
        "user": USER,
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "start": start,
        "end": end,
        "days": days,
        "activity": activity,
        "languages": dict(languages.most_common()),
        "language_repos": sum(languages.values()),
        "unclassified_repos": sum(repo["language"] is None for repo in owned),
        "active_repositories": dict(active.most_common(5)),
        "commit_sample": len(unique),
        "commit_total": total_commits,
        "sources": [
            f"https://github.com/users/{USER}/contributions",
            f"https://api.github.com/users/{USER}/repos?type=owner&per_page=100",
            "https://api.github.com/search/commits?q=" + urllib.parse.quote(commits_query),
            "https://api.github.com/search/issues?q=" + urllib.parse.quote(f"author:{USER} created:{period} is:public"),
        ],
        "definitions": {
            "calendar": "Latest 365 days returned by GitHub's public contribution calendar; GitHub contribution rules apply.",
            "activity": "Public GitHub search totals. Commits use committer-date; PRs and issues use creation date. Search indexing rules apply.",
            "languages": "Count of public, owned, non-fork, non-archived repositories by primary language. Repositories with no language excluded.",
            "active_repositories": "Top repositories by unique (repository, SHA) matches from public commit search, at most 1000 results.",
        },
    }


def text(x, y, content, size=14, fill=TEXT, anchor="start", weight="400"):
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{html.escape(str(content))}</text>')


def card(title, subtitle, body, height, data):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}" role="img">'
            f'<title>{html.escape(title)}</title><desc>{html.escape(subtitle)}</desc>'
            '<g font-family="Arial, sans-serif">'
            f'<rect x="1" y="1" width="998" height="{height-2}" rx="18" fill="{BG}" stroke="#34364e"/>'
            + text(32, 40, title, 23, TEXT, weight="700")
            + text(32, 67, subtitle, 13, MUTED)
            + body
            + text(968, height-16, f'Updated {data["updated"]}', 11, MUTED, "end")
            + '</g></svg>')


def bars(title, subtitle, rows, data, suffix="", footnote=""):
    height = max(240, 120 + 47 * len(rows) + (25 if footnote else 0))
    maximum = max((value for _, value in rows), default=1) or 1
    body = ""
    for index, (label, value) in enumerate(rows):
        y = 111 + index * 47
        body += text(32, y+5, label, 15)
        body += f'<rect x="285" y="{y-10}" width="580" height="18" rx="4" fill="#272b43"/>'
        width = 580 * value / maximum
        if value:
            body += f'<rect x="285" y="{y-10}" width="{width:.2f}" height="18" rx="4" fill="{COLORS[index % len(COLORS)]}"/>'
        body += text(955, y+5, f"{value:,}{suffix}", 16, TEXT, "end")
    if not rows:
        body += text(32, 130, "No matching public records in this period", 16, MUTED)
    if footnote:
        body += text(32, height-43, footnote, 12, MUTED)
    return card(title, subtitle, body, height, data)


def render(data):
    days = data["days"]
    total = sum(days.values())
    active_days = sum(value > 0 for value in days.values())
    body = text(32, 114, f"{total:,} contributions", 25, COLORS[0], weight="700")
    body += text(370, 114, f"{active_days} active days", 20, COLORS[1])
    body += text(700, 114, f"Peak {max(days.values()):,} / day", 20, COLORS[2])
    first = dt.date.fromisoformat(data["start"])
    first_sunday = first - dt.timedelta(days=(first.weekday()+1) % 7)
    maximum = max(days.values())
    levels = ["#282b41", "#514568", "#83648e", "#b685b2", "#f1acd4"]
    months_seen = set()
    for day, count in days.items():
        date = dt.date.fromisoformat(day)
        week = (date-first_sunday).days // 7
        weekday = (date.weekday()+1) % 7
        x, y = 70 + week * 17, 161 + weekday * 17
        level = 0 if count == 0 else min(4, 1 + (count-1)*4 // max(maximum, 1))
        body += (f'<rect x="{x}" y="{y}" width="13" height="13" rx="3" fill="{levels[level]}">'
                 f'<title>{day} · {count} contributions</title></rect>')
        month = day[:7]
        if month not in months_seen:
            if date.day <= 7 or not months_seen:
                body += text(x, 149, date.strftime("%b"), 11, MUTED)
            months_seen.add(month)
    for label, row in [("Mon", 1), ("Wed", 3), ("Fri", 5)]:
        body += text(31, 171+row*17, label, 10, MUTED)
    body += text(32, 315, f'{data["start"]} — {data["end"]} · GitHub contribution calendar', 12, MUTED)
    body += text(730, 315, "Less", 11, MUTED)
    for i, color in enumerate(levels):
        body += f'<rect x="{765+i*18}" y="304" width="13" height="13" rx="3" fill="{color}"/>'
    body += text(865, 315, "More", 11, MUTED)
    assets = {"calendar.svg": card("Contribution calendar", "Daily contributions over the latest 365 days", body, 355, data)}

    monthly = collections.Counter()
    for day, count in days.items():
        monthly[day[:7]] += count
    months = sorted(monthly)
    peak = max(monthly.values()) or 1
    body = ""
    for tick in range(5):
        value = peak * tick / 4
        y = 258 - tick * 37
        body += f'<path d="M70 {y}H948" stroke="#33364d" stroke-width="1"/>'
        body += text(56, y+4, f"{value:g}", 11, MUTED, "end")
    points = []
    for i, month in enumerate(months):
        x = 78 + i * 860 / max(len(months)-1, 1)
        y = 258 - monthly[month] / peak * 148
        points.append((x, y, month))
    body += '<polyline points="' + ' '.join(f'{x:.1f},{y:.1f}' for x,y,_ in points) + f'" fill="none" stroke="{COLORS[1]}" stroke-width="3"/>'
    for x, y, month in points:
        body += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{COLORS[1]}"/>'
        body += text(round(x,1), round(y-12,1), monthly[month], 12, TEXT, "middle")
        body += text(round(x,1), 283, month[2:], 11, MUTED, "middle")
    body += text(32, 316, "Contributions / month · First and latest months may be partial", 12, MUTED)
    assets["monthly.svg"] = card("Contribution trend", f'{data["start"]} — {data["end"]} · Monthly totals', body, 355, data)
    assets["activity.svg"] = bars("Public activity", f'{data["start"]} — {data["end"]} · GitHub search counts', list(data["activity"].items()), data, footnote="Commits by committer date · Pull requests and issues by creation date · Search indexing applies")
    languages = list(data["languages"].items())
    if len(languages) > 6:
        languages = languages[:5] + [("Other", sum(count for _,count in languages[5:]))]
    assets["languages.svg"] = bars("Repository languages", f'{data["language_repos"]} public non-fork, non-archived repositories with a detected primary language', languages, data, footnote=f'Count of repositories by primary language · {data["unclassified_repos"]} without a detected language excluded')
    rows = list(data["active_repositories"].items())
    rows = [(repo.removeprefix(USER+"/"), count) for repo,count in rows]
    sample_note = f'{data["commit_sample"]:,} indexed commit matches'
    if data["commit_total"] > 1000:
        sample_note += f' sampled from {data["commit_total"]:,} results (GitHub search cap)'
    assets["repositories.svg"] = bars("Repositories by public commits", f'{data["start"]} — {data["end"]} · Top 5 by indexed author commit matches', rows, data, footnote=sample_note + " · Counts can include fork histories")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in assets.items():
        (OUT / name).write_text(content+"\n", encoding="utf-8")
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"contributions": total, "active_days": active_days, "activity": data["activity"], "languages": data["languages"], "repositories": data["active_repositories"]}, ensure_ascii=False))


if __name__ == "__main__":
    render(collect())
