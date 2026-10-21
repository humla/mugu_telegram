from req import Request, SimpleResponse
from util import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse
import datetime
import html
import re
import xml.etree.ElementTree as ET
import zoneinfo

import requests

# Wikimedia asks API clients to identify themselves.
HEADERS = {"User-Agent": "mugu_telegram/1.0 (https://github.com/humla/mugu_telegram)"}


def getJson(url, params=None):
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def shorten(text, limit):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


class LinkListResponse(SimpleResponse):
    """HTML response whose many links should not expand into a preview of the first one."""

    def __init__(self, lines):
        super().__init__(lines, parse_mode="HTML")

    def disableLinkPreview(self):
        return True


class WikiRequest(Request):
    def supportedCommands(self):
        return {'wiki'}

    def helpString(self):
        return "wiki <topic>: Short Wikipedia summary, e.g. wiki alan turing"

    def handleRequest(self):
        query = " ".join(self.requestParams[1:]).strip()
        if not query:
            return SimpleResponse("What should I look up? e.g. <code>wiki alan turing</code>", parse_mode="HTML")
        try:
            # Messages arrive upper-cased; Wikipedia capitalises the first letter itself.
            page = WikiRequest.summary(query.lower())
            if page is None:
                # No article by that exact title: search ranks by popularity, so it
                # is only the fallback ('mercury' would otherwise be Freddie Mercury).
                titles = getJson("https://en.wikipedia.org/w/api.php", {
                    "action": "opensearch", "search": query, "limit": 1, "namespace": 0, "format": "json"})[1]
                page = WikiRequest.summary(titles[0]) if titles else None
        except (requests.RequestException, ValueError, IndexError) as e:
            logging.error(f"Wikipedia lookup failed for '{query}': {e}")
            return SimpleResponse("Sorry, I could not reach Wikipedia right now.")
        if page is None:
            return SimpleResponse(f"I could not find a Wikipedia article for '{html.escape(query.lower())}'.")

        lines = [f"📖 <b>{html.escape(page.get('title', query))}</b>"]
        if page.get("description"):
            lines.append(f"<i>{html.escape(page['description'])}</i>")
        lines.append("")
        if page.get("type") == "disambiguation":
            # Disambiguation extracts are one option per line; keep the lines.
            extract = re.sub(r"(refers? to:)\s*", r"\1\n", page.get("extract") or "")
            options = [shorten(line, 120) for line in extract.splitlines() if line.strip()]
            lines += [html.escape(option) for option in options[:8]]
        else:
            lines.append(html.escape(shorten(page.get("extract"), 900)))
        url = (page.get("content_urls") or {}).get("desktop", {}).get("page")
        if url:
            lines += ["", f'<a href="{html.escape(url)}">Read more on Wikipedia</a>']
        return SimpleResponse(lines, parse_mode="HTML")

    @staticmethod
    def summary(title):
        """Page summary for an exact title (redirects followed), or None if there is no such page."""
        resp = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_"), safe=""),
                            headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


class DefineRequest(Request):
    MEANINGS = 3
    DEFINITIONS_PER_MEANING = 2

    def supportedCommands(self):
        return {'define'}

    def helpString(self):
        return "define <word>: Dictionary definition and pronunciation, e.g. define serendipity"

    def handleRequest(self):
        word = " ".join(self.requestParams[1:]).strip().lower()
        if not word:
            return SimpleResponse("Which word? e.g. <code>define serendipity</code>", parse_mode="HTML")
        try:
            resp = requests.get("https://api.dictionaryapi.dev/api/v2/entries/en/" + quote(word), headers=HEADERS, timeout=15)
        except requests.RequestException as e:
            logging.error(f"Dictionary lookup failed for '{word}': {e}")
            return SimpleResponse("Sorry, I could not reach the dictionary right now.")
        # Unknown words come back as 404, or as 530 from the service's proxy.
        if resp.status_code in (404, 530):
            return SimpleResponse(f"I could not find a definition for '{html.escape(word)}'.")
        if resp.status_code != 200:
            logging.error(f"Dictionary returned HTTP {resp.status_code} for '{word}'")
            return SimpleResponse("Sorry, I could not reach the dictionary right now.")

        entries = resp.json()
        phonetic = next((e.get("phonetic") for e in entries if e.get("phonetic")), None) or next(
            (p.get("text") for e in entries for p in e.get("phonetics", []) if p.get("text")), None)
        header = f"📚 <b>{html.escape(entries[0].get('word', word))}</b>"
        if phonetic:
            header += f"  {html.escape(phonetic)}"
        lines = [header]

        meanings = [m for e in entries for m in e.get("meanings", [])]
        for meaning in meanings[:self.MEANINGS]:
            lines += ["", f"<i>{html.escape(meaning.get('partOfSpeech', ''))}</i>"]
            for i, definition in enumerate(meaning.get("definitions", [])[:self.DEFINITIONS_PER_MEANING], 1):
                lines.append(f"{i}. {html.escape(definition.get('definition', ''))}")
                if definition.get("example"):
                    lines.append(f"   <i>“{html.escape(definition['example'])}”</i>")
        return SimpleResponse(lines, parse_mode="HTML")


class TimeRequest(Request):
    HOME = "Europe/London"

    def supportedCommands(self):
        return {'time'}

    def helpString(self):
        return "time <city>: Current time anywhere, e.g. time kathmandu (just 'time' for UK time)"

    def handleRequest(self):
        place = " ".join(self.requestParams[1:]).strip()
        if not place:
            return SimpleResponse(TimeRequest.describe(self.HOME, "London"), parse_mode="HTML")
        zone, label = TimeRequest.findZone(place)
        if zone is None:
            return SimpleResponse(f"I could not find a place called '{html.escape(place.title())}'.")
        return SimpleResponse(TimeRequest.describe(zone, label), parse_mode="HTML")

    @staticmethod
    def findZone(place):
        """Return (IANA zone, display label) for a city name, or (None, None)."""
        wanted = place.lower().replace(" ", "_")
        for zone in zoneinfo.available_timezones():
            if zone.lower().rsplit("/", 1)[-1] == wanted:
                return zone, zone.rsplit("/", 1)[-1].replace("_", " ")
        # Most cities have no zone of their own (Delhi is Asia/Kolkata), so geocode them.
        try:
            results = getJson("https://geocoding-api.open-meteo.com/v1/search",
                              {"name": place, "count": 1}).get("results") or []
        except (requests.RequestException, ValueError) as e:
            logging.error(f"Geocoding failed for '{place}': {e}")
            return None, None
        if not results or not results[0].get("timezone"):
            return None, None
        city = results[0]
        label = ", ".join(p for p in (city.get("name"), city.get("country")) if p)
        return city["timezone"], label

    @staticmethod
    def describe(zone, label, now=None):
        now = now or datetime.datetime.now(datetime.timezone.utc)
        local = now.astimezone(zoneinfo.ZoneInfo(zone))
        home = now.astimezone(zoneinfo.ZoneInfo(TimeRequest.HOME))
        line = f"🕐 <b>{html.escape(label)}</b>: {local:%H:%M} on {local:%a} {local.day} {local:%b} ({TimeRequest.offsetLabel(local)})"
        diff_minutes = round((local.utcoffset() - home.utcoffset()).total_seconds() / 60)
        if zone != TimeRequest.HOME and diff_minutes:
            hours, minutes = divmod(abs(diff_minutes), 60)
            span = f"{hours}h" + (f"{minutes:02d}m" if minutes else "")
            line += f"\n{span} {'ahead of' if diff_minutes > 0 else 'behind'} UK time"
        return line

    @staticmethod
    def offsetLabel(local):
        total = round(local.utcoffset().total_seconds() / 60)
        sign = "+" if total >= 0 else "-"
        hours, minutes = divmod(abs(total), 60)
        return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


class NewsRequest(Request):
    BBC_FEED = "https://feeds.bbci.co.uk/news/rss.xml"
    BBC_COUNT = 8
    HN_API = "https://hacker-news.firebaseio.com/v0"
    HN_COUNT = 10
    HN_LISTS = {"": "topstories", "top": "topstories", "best": "beststories", "new": "newstories",
                "ask": "askstories", "show": "showstories"}

    def supportedCommands(self):
        return {'news', 'hn'}

    def helpString(self):
        return ("news : Top BBC News headlines\n"
                "hn : Hacker News front page (also hn best, hn new, hn ask, hn show)")

    def handleRequest(self):
        if self.command() == "news":
            return NewsRequest.bbcHeadlines()
        variant = " ".join(self.requestParams[1:]).strip().lower()
        if variant not in self.HN_LISTS:
            return SimpleResponse("Try <code>hn</code>, <code>hn best</code>, <code>hn new</code>, <code>hn ask</code> or <code>hn show</code>.",
                                  parse_mode="HTML")
        return NewsRequest.hackerNews(variant or "top")

    @staticmethod
    def bbcHeadlines():
        try:
            resp = requests.get(NewsRequest.BBC_FEED, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            items = ET.fromstring(resp.content).iter("item")
        except (requests.RequestException, ET.ParseError) as e:
            logging.error(f"BBC News feed failed: {e}")
            return SimpleResponse("Sorry, I could not fetch the BBC News headlines right now.")
        lines = ["📰 <b>BBC News</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for item in list(items)[:NewsRequest.BBC_COUNT]:
            title, link = item.findtext("title", "").strip(), item.findtext("link", "").strip()
            description = shorten(item.findtext("description", ""), 160)
            lines.append(f'• <a href="{html.escape(link)}">{html.escape(title)}</a>')
            if description:
                lines.append(f"  <i>{html.escape(description)}</i>")
        return LinkListResponse(lines)

    @staticmethod
    def hackerNews(variant):
        try:
            ids = getJson(f"{NewsRequest.HN_API}/{NewsRequest.HN_LISTS[variant]}.json")[:NewsRequest.HN_COUNT]
            # One request per story; fetch them side by side rather than one after another.
            with ThreadPoolExecutor(max_workers=NewsRequest.HN_COUNT) as pool:
                stories = [s for s in pool.map(lambda i: getJson(f"{NewsRequest.HN_API}/item/{i}.json"), ids) if s]
        except (requests.RequestException, ValueError) as e:
            logging.error(f"Hacker News fetch failed: {e}")
            return SimpleResponse("Sorry, I could not reach Hacker News right now.")

        title = "Hacker News" if variant == "top" else f"Hacker News · {variant}"
        lines = [f"🟧 <b>{title}</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for rank, story in enumerate(stories, 1):
            discussion = f"https://news.ycombinator.com/item?id={story['id']}"
            url = story.get("url") or discussion
            domain = urlparse(story["url"]).netloc.removeprefix("www.") if story.get("url") else ""
            heading = f'{rank}. <a href="{html.escape(url)}">{html.escape(story.get("title", ""))}</a>'
            if domain:
                heading += f" ({html.escape(domain)})"
            comments = story.get("descendants", 0)
            comments_text = f'💬 {comments} comment{"s" if comments != 1 else ""}'
            if story.get("url"):
                # Link the discussion separately; for Ask HN posts the heading already does.
                comments_text = f'<a href="{discussion}">{comments_text}</a>'
            lines.append(heading)
            lines.append(f'    ▲ {story.get("score", 0)} · {comments_text}')
        return LinkListResponse(lines)
