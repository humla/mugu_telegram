from req import Request, Response, SimpleResponse, EmptyResponse
from util import logging
import xml.etree.ElementTree as ET
import requests

class NatureRequest(Request):
    FEED_URL = 'https://www.nature.com/nature.rss'
    LATEST_COUNT = 5

    def supportedCommands(self):
        return {'nature'}

    def helpString(self):
        return "nature : Shows the latest articles from Nature magazine"

    def handleRequest(self):
        logging.info("Nature request")
        response = requests.get(NatureRequest.FEED_URL, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
        if response.status_code != 200:
            return SimpleResponse("Sorry, I could not fetch the Nature feed right now (HTTP %d)." % response.status_code)

        root = ET.fromstring(response.content)
        ns = {'rss1': 'http://purl.org/rss/1.0/', 'dc': 'http://purl.org/dc/elements/1.1/'}
        articles = []
        for item in root.findall('rss1:item', ns)[:NatureRequest.LATEST_COUNT]:
            title = item.findtext('rss1:title', default='', namespaces=ns).strip()
            link = item.findtext('rss1:link', default='', namespaces=ns).strip()
            date = item.findtext('dc:date', default='', namespaces=ns).strip()[:10] or 'online'
            if title:
                articles.append("%s (%s)\n%s" % (title, date, link))

        if not articles:
            return SimpleResponse("Sorry, I could not find any articles in the Nature feed.")
        return SimpleResponse(articles)
