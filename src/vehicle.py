from req import Request, Response, SimpleResponse
from util import logging
import datetime
import html
import os
import re
import time

import requests

# DVLA Vehicle Enquiry Service: tax status (and a basic MOT status).
# https://developer-portal.driver-vehicle-licensing.api.gov.uk/apis/vehicle-enquiry-service/vehicle-enquiry-service-description.html
DVLA_URL = "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles"
DVLA_KEY_FILE = os.path.join(os.path.dirname(__file__), "../res/dvla.key")

# DVSA MOT History API: MOT expiry, test results and defects.
# https://documentation.history.mot.api.gov.uk/
MOT_URL = "https://history.mot.api.gov.uk/v1/trade/vehicles/registration/"
MOT_SCOPE = "https://tapi.dvsa.gov.uk/.default"
MOT_KEY_FILE = os.path.join(os.path.dirname(__file__), "../res/mot.key")

HISTORY_COUNT = 5
DEFECT_COUNT = 6
DEFECT_EMOJI = {"DANGEROUS": "⛔", "MAJOR": "❌", "FAIL": "❌", "MINOR": "🟠", "ADVISORY": "🟡"}


class VehicleLookupError(Exception):
    pass


def readKeyLines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def dvlaApiKey():
    lines = readKeyLines(DVLA_KEY_FILE)
    return lines[0] if lines else None


def motCredentials():
    """res/mot.key: client ID, client secret, API key and token URL, one per line."""
    lines = readKeyLines(MOT_KEY_FILE)
    if len(lines) < 4:
        return None
    return dict(zip(("client_id", "client_secret", "api_key", "token_url"), lines))


def fetchDvla(registration):
    resp = requests.post(DVLA_URL, json={"registrationNumber": registration},
                         headers={"x-api-key": dvlaApiKey()}, timeout=15)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise VehicleLookupError(f"DVLA returned HTTP {resp.status_code}")
    return resp.json()


_mot_token = {"value": None, "expires": 0}


def motAccessToken(credentials):
    if time.time() < _mot_token["expires"]:
        return _mot_token["value"]
    resp = requests.post(credentials["token_url"], data={
        "grant_type": "client_credentials",
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "scope": MOT_SCOPE,
    }, timeout=15)
    if resp.status_code != 200:
        raise VehicleLookupError(f"MOT token request returned HTTP {resp.status_code}")
    body = resp.json()
    _mot_token["value"] = body["access_token"]
    _mot_token["expires"] = time.time() + int(body.get("expires_in", 3600)) - 60
    return _mot_token["value"]


def fetchMot(registration):
    credentials = motCredentials()
    resp = requests.get(MOT_URL + registration, timeout=15, headers={
        "Authorization": f"Bearer {motAccessToken(credentials)}",
        "X-API-Key": credentials["api_key"],
    })
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise VehicleLookupError(f"MOT History API returned HTTP {resp.status_code}")
    return resp.json()


def parseDate(text):
    try:
        return datetime.date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def formatDate(day):
    return f"{day.day} {day:%b %Y}"


def untilString(day, today):
    days = (day - today).days
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} ago"
    if days == 0:
        return "ends today"
    return f"{days} day{'s' if days != 1 else ''} left"


def formatMileage(test):
    value = str(test.get("odometerValue") or "")
    if test.get("odometerResultType") != "READ" or not value.isdigit():
        return ""
    unit = "km" if test.get("odometerUnit") == "KM" else "mi"
    return f"{int(value):,} {unit}"


class VehicleRequest(Request):
    def supportedCommands(self):
        return {'car'}

    def helpString(self):
        return "car <registration>: Car tax and MOT status, last MOT result and recent MOT history, e.g. car AB12CDE"

    def handleRequest(self):
        registration = re.sub(r"[^A-Z0-9]", "", "".join(self.requestParams[1:]).upper())
        if not registration:
            return SimpleResponse("Which car? e.g. <code>car AB12CDE</code>", parse_mode="HTML")
        if not re.fullmatch(r"[A-Z0-9]{2,8}", registration):
            return SimpleResponse(f"'{html.escape(registration)}' does not look like a UK registration number.")

        has_dvla, has_mot = bool(dvlaApiKey()), motCredentials() is not None
        if not has_dvla and not has_mot:
            return SimpleResponse(
                "🚗 The <b>car</b> command needs at least one government API key:\n\n"
                "• <b>MOT history</b> (DVSA): register at "
                "<a href=\"https://documentation.history.mot.api.gov.uk/mot-history-api/register\">history.mot.api.gov.uk</a>, "
                "then put the client ID, client secret, API key and token URL on separate lines in <code>res/mot.key</code>\n"
                "• <b>Car tax</b> (DVLA Vehicle Enquiry Service): put the API key in <code>res/dvla.key</code>. "
                "DVLA has paused new registrations, so this one may not be available yet.",
                parse_mode="HTML")

        logging.info(f"Vehicle request for {registration}")
        dvla = mot = None
        errors = []
        for enabled, fetch, label in ((has_dvla, fetchDvla, "DVLA"), (has_mot, fetchMot, "MOT history")):
            if not enabled:
                continue
            try:
                result = fetch(registration)
            except (VehicleLookupError, requests.RequestException, ValueError, KeyError) as e:
                logging.error(f"{label} lookup failed for {registration}: {e}")
                errors.append(label)
                continue
            if label == "DVLA":
                dvla = result
            else:
                mot = result

        if dvla is None and mot is None:
            if errors:
                return SimpleResponse(f"Sorry, I could not reach {' or '.join(errors)} right now.")
            return SimpleResponse(f"I could not find a vehicle with registration {html.escape(registration)}.")
        return VehicleResponse(registration, dvla, mot, has_dvla, has_mot, errors)


class VehicleResponse(Response):
    def __init__(self, registration, dvla, mot, has_dvla, has_mot, errors, today=None):
        self.registration = registration
        self.dvla = dvla or {}
        self.mot = mot or {}
        self.has_dvla = has_dvla
        self.has_mot = has_mot
        self.errors = errors
        self.today = today or datetime.date.today()

    def _description(self):
        make = self.mot.get("make") or self.dvla.get("make") or ""
        model = self.mot.get("model") or ""
        colour = self.mot.get("primaryColour") or self.dvla.get("colour") or ""
        fuel = self.mot.get("fuelType") or self.dvla.get("fuelType") or ""
        first_reg = self.mot.get("registrationDate") or self.mot.get("firstUsedDate") or self.dvla.get("monthOfFirstRegistration") or ""
        parts = [" ".join(p.title() for p in (make, model) if p), colour.title(), fuel.title(), first_reg[:4]]
        return ", ".join(html.escape(p) for p in parts if p)

    def _taxLine(self):
        if not self.has_dvla:
            return "💷 <b>Tax</b>: <i>unavailable (needs a DVLA key in res/dvla.key)</i>"
        if not self.dvla:
            reason = "DVLA lookup failed" if "DVLA" in self.errors else "no DVLA record"
            return f"💷 <b>Tax</b>: <i>unknown ({reason})</i>"
        status = self.dvla.get("taxStatus") or "Unknown"
        due = parseDate(self.dvla.get("taxDueDate"))
        if status == "Taxed":
            text = "✅ Taxed"
            if due:
                text += f" until {formatDate(due)} ({untilString(due, self.today)})"
        elif status == "SORN":
            text = "🅿️ SORN (declared off the road)"
        else:
            text = f"❌ {html.escape(status)}"
            if due:
                text += f", tax was due {formatDate(due)}"
        return f"💷 <b>Tax</b>: {text}"

    def _motTests(self):
        tests = self.mot.get("motTests") or []
        return sorted(tests, key=lambda t: t.get("completedDate") or "", reverse=True)

    def _motLine(self):
        first_due = parseDate(self.mot.get("motTestDueDate"))
        if first_due:
            return f"🔧 <b>MOT</b>: 🆕 Not needed yet, first MOT due {formatDate(first_due)} ({untilString(first_due, self.today)})"

        expiries = [parseDate(t.get("expiryDate")) for t in self._motTests() if t.get("testResult") == "PASSED"]
        expiry = max((d for d in expiries if d), default=None)
        if expiry is None:
            # Without MOT history, fall back to DVLA's MOT summary.
            expiry = parseDate(self.dvla.get("motExpiryDate"))
        if expiry:
            state = "✅ Valid until" if expiry >= self.today else "❌ Expired on"
            return f"🔧 <b>MOT</b>: {state} {formatDate(expiry)} ({untilString(expiry, self.today)})"

        status = self.dvla.get("motStatus")
        if status:
            return f"🔧 <b>MOT</b>: {html.escape(status)}"
        if not self.has_mot:
            return "🔧 <b>MOT</b>: <i>unavailable (needs a DVSA key in res/mot.key)</i>"
        return "🔧 <b>MOT</b>: <i>no MOT record found</i>"

    def toTelegramString(self):
        lines = [f"🚗 <b>{html.escape(self.registration)}</b>"]
        description = self._description()
        if description:
            lines[0] += f" · {description}"
        lines += ["━━━━━━━━━━━━━━━━━━━━", self._taxLine(), self._motLine()]

        if self.mot.get("hasOutstandingRecall") == "Yes":
            lines.append("⚠️ <b>Outstanding safety recall</b>: check with the manufacturer or gov.uk/check-vehicle-recall")
        if self.dvla.get("markedForExport"):
            lines.append("⚠️ Marked for export")

        tests = self._motTests()
        if tests:
            last = tests[0]
            done = parseDate(last.get("completedDate"))
            result = "✅ Passed" if last.get("testResult") == "PASSED" else "❌ Failed"
            mileage = formatMileage(last)
            lines += ["", f"<b>Last MOT</b> ({formatDate(done) if done else 'date unknown'}): {result}"
                      + (f" at {mileage}" if mileage else "")]
            defects = last.get("defects") or []
            for defect in defects[:DEFECT_COUNT]:
                kind = "DANGEROUS" if defect.get("dangerous") else (defect.get("type") or "")
                emoji = DEFECT_EMOJI.get(kind, "•")
                lines.append(f"{emoji} {html.escape(kind.title())}: {html.escape(defect.get('text') or '')}")
            if len(defects) > DEFECT_COUNT:
                lines.append(f"<i>…and {len(defects) - DEFECT_COUNT} more</i>")

            if len(tests) > 1:
                lines += ["", "<b>MOT history</b>"]
                for test in tests[:HISTORY_COUNT]:
                    done = parseDate(test.get("completedDate"))
                    mark = "✅" if test.get("testResult") == "PASSED" else "❌"
                    mileage = formatMileage(test)
                    lines.append(f"{mark} {formatDate(done) if done else '?'}" + (f" · {mileage}" if mileage else ""))

        if self.errors:
            lines += ["", f"<i>Could not reach {' or '.join(self.errors)}; some details may be missing.</i>"]
        return lines

    def parseMode(self):
        return "HTML"
