from req import Request, Response, SimpleResponse, EmptyResponse
from util import logging
import html
import re
import xml.etree.ElementTree as ET
import requests

class NatureRequest(Request):
    LATEST_COUNT = 5
    # Each feed: (source label, feed url, title suffix to strip)
    FEEDS = [
        ('Nature', 'https://www.nature.com/nature.rss', ''),
        ('NatGeo', 'https://news.google.com/rss/search?q=site:nationalgeographic.com+when:30d&hl=en-GB&gl=GB&ceid=GB:en', ' - National Geographic'),
    ]

    def supportedCommands(self):
        return {'nature'}

    def helpString(self):
        return "nature : Shows the latest articles from Nature and National Geographic"

    def handleRequest(self):
        logging.info("Nature request")
        articles = []
        for label, url, suffix in NatureRequest.FEEDS:
            articles.extend(NatureRequest.latestFromFeed(label, url, suffix))
        if not articles:
            return SimpleResponse("Sorry, I could not fetch the article feeds right now.")
        return SimpleResponse(articles)

    def latestFromFeed(label, url, titleSuffix):
        try:
            response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            if response.status_code != 200:
                logging.info(f"{label} feed failed with status {response.status_code}")
                return []
            # Works for both RSS 1.0 (rdf, namespaced tags) and RSS 2.0
            root = ET.fromstring(response.content)
            items = [el for el in root.iter() if el.tag.split('}')[-1] == 'item']
            articles = []
            for item in items[:NatureRequest.LATEST_COUNT]:
                title = NatureRequest.childText(item, 'title')
                link = NatureRequest.childText(item, 'link')
                date = NatureRequest.childText(item, 'date') or NatureRequest.childText(item, 'pubDate')
                summary = NatureRequest.summaryText(item)
                if not title:
                    continue
                title = title.strip()
                if titleSuffix and title.endswith(titleSuffix):
                    title = title[:-len(titleSuffix)].strip()
                line = "%s: %s (%s)" % (label, title, NatureRequest.formatDate(date))
                if summary and not summary.startswith(title):
                    line += "\n" + summary
                articles.append(line + "\n" + link)
            return articles
        except (ET.ParseError, requests.RequestException) as e:
            logging.info(f"{label} feed could not be read: {e}")
            return []

    def childText(item, localName):
        for child in item:
            if child.tag.split('}')[-1] == localName and child.text:
                return child.text.strip()
        return ''

    SUMMARY_LENGTH = 200

    def summaryText(item):
        """Extract a plain-text summary: Nature's content:encoded or the generic RSS description."""
        for child in item:
            if child.tag.split('}')[-1] in ('encoded', 'description') and child.text:
                text = re.sub(r'<[^>]+>', ' ', child.text)  # strip HTML
                text = html.unescape(text)
                text = re.sub(r'\s+', ' ', text).strip()
                text = re.sub(r'^(Nature, Published online: [0-9]+ [A-Za-z]+ [0-9]+;?\s*)?(doi:\S+\s*)', '', text)
                if len(text) > NatureRequest.SUMMARY_LENGTH:
                    text = text[:NatureRequest.SUMMARY_LENGTH].rsplit(' ', 1)[0] + '…'
                return text
        return ''

    def formatDate(rawDate):
        if not rawDate:
            return 'online'
        rawDate = rawDate.strip()
        if ',' in rawDate:  # RFC 822 style: "Thu, 16 Oct 2026 12:34:00 +0000"
            parts = rawDate.split(', ', 1)[-1].split(' ')
            return ' '.join(parts[:3])
        return rawDate[:10]  # ISO style: "2026-10-16T..."
