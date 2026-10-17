from req import Request, Response, SimpleResponse, EmptyResponse
from util import logging
from rail import RailRequest, loadStationCodes
import json
import os
import datetime
import html

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None

USER_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "../res/user_settings.json")


def loadAllUserSettings():
    if not os.path.exists(USER_SETTINGS_FILE):
        return {}
    try:
        with open(USER_SETTINGS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading user settings: {e}")
        return {}


def getUserSettings(user_id):
    if not user_id:
        return {}
    all_settings = loadAllUserSettings()
    return all_settings.get(str(user_id), {})


def saveUserCommute(user_id, home=None, home_name=None, work=None, work_name=None):
    if not user_id:
        return
    all_settings = loadAllUserSettings()
    user_str = str(user_id)
    if user_str not in all_settings:
        all_settings[user_str] = {}

    if home is not None:
        all_settings[user_str]["home"] = home
        all_settings[user_str]["home_name"] = home_name or home
    if work is not None:
        all_settings[user_str]["work"] = work
        all_settings[user_str]["work_name"] = work_name or work

    try:
        os.makedirs(os.path.dirname(USER_SETTINGS_FILE), exist_ok=True)
        with open(USER_SETTINGS_FILE, "w") as f:
            json.dump(all_settings, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving user settings: {e}")


class StationChoiceForSettingResponse(Response):
    """Buttons when 'set home <name>' or 'set work <name>' has multiple station matches."""
    def __init__(self, setting_type, query, matches):
        self.setting_type = setting_type  # 'home' or 'work'
        self.query = query
        self.matches = matches  # dict of {name: code}

    def toTelegramString(self):
        title_type = self.setting_type.title()
        title_query = html.escape(self.query.title())
        return [f"Multiple stations match <b>'{title_query}'</b>.\nSelect your <b>{title_type}</b> station:"]

    def replyMarkup(self):
        if not InlineKeyboardMarkup or not InlineKeyboardButton:
            return None
        keyboard = []
        row = []
        for name, code in sorted(self.matches.items())[:8]:
            btn_text = f"📍 {name.title()} ({code})"
            cb_data = f"set{self.setting_type}:{code}:{name.title()}"
            row.append(InlineKeyboardButton(btn_text, callback_data=cb_data))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return InlineKeyboardMarkup(keyboard)

    def parseMode(self):
        return "HTML"


class CommuteRequest(Request):
    def __init__(self, requestParams, user_id=None):
        super().__init__(requestParams)
        self.user_id = user_id

    def supportedCommands(self):
        return {'commute', 'set', 'home', 'work', 'my'}

    def helpString(self):
        return ("commute : Realtime departures for your daily commute (detects morning/evening direction)\n"
                "set home <station> : Set your home station (e.g. set home dartford)\n"
                "set work <station> : Set your work station (e.g. set work london bridge)\n"
                "my stations : View your saved commute stations")

    def handleRequestBase(self):
        cmd = self.command()
        # Verify valid command combinations
        if cmd == 'set':
            if len(self.requestParams) > 1 and self.requestParams[1].lower() in ('home', 'work'):
                return self.handleRequest()
            return EmptyResponse()
        elif cmd in ('home', 'work'):
            # allow 'home <station>' and 'work <station>' as shortcuts
            if len(self.requestParams) > 1:
                return self.handleRequest()
            return EmptyResponse()
        elif cmd == 'my':
            if len(self.requestParams) > 1 and self.requestParams[1].lower() in ('stations', 'commute'):
                return self.handleRequest()
            return EmptyResponse()
        elif cmd == 'commute':
            return self.handleRequest()
        return EmptyResponse()

    def handleRequest(self):
        cmd = self.command()

        # Handle 'my stations' or 'my commute'
        if cmd == 'my' or (cmd == 'commute' and len(self.requestParams) > 1 and self.requestParams[1].lower() in ('settings', 'stations')):
            return self._handleShowSettings()

        # Handle 'set home <station>' or 'home <station>'
        if (cmd == 'set' and len(self.requestParams) > 1 and self.requestParams[1].lower() == 'home') or cmd == 'home':
            words = self.requestParams[2:] if cmd == 'set' else self.requestParams[1:]
            return self._handleSetStation('home', words)

        # Handle 'set work <station>' or 'work <station>'
        if (cmd == 'set' and len(self.requestParams) > 1 and self.requestParams[1].lower() == 'work') or cmd == 'work':
            words = self.requestParams[2:] if cmd == 'set' else self.requestParams[1:]
            return self._handleSetStation('work', words)

        # Handle 'commute'
        if cmd == 'commute':
            return self._handleCommute()

        return EmptyResponse()

    def _handleShowSettings(self):
        settings = getUserSettings(self.user_id)
        home = settings.get("home")
        home_name = settings.get("home_name", home)
        work = settings.get("work")
        work_name = settings.get("work_name", work)

        home_str = f"<b>{html.escape(home_name)}</b> (<code>{home}</code>)" if home else "<i>Not set</i>"
        work_str = f"<b>{html.escape(work_name)}</b> (<code>{work}</code>)" if work else "<i>Not set</i>"

        msg = (
            "🚉 <b>Your Commute Settings</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🏠 <b>Home</b>: {home_str}\n"
            f"🏢 <b>Work</b>: {work_str}\n\n"
            "💡 <i>To change, type:</i>\n"
            "• <code>set home &lt;station&gt;</code>\n"
            "• <code>set work &lt;station&gt;</code>"
        )
        return SimpleResponse(msg, parse_mode="HTML")

    def _handleSetStation(self, setting_type, words):
        if not words:
            return SimpleResponse(
                f"Please specify a station for your {setting_type}, e.g. <code>set {setting_type} dartford</code>",
                parse_mode="HTML"
            )

        query = " ".join(words)
        code, matches, err = RailRequest.resolveStation(query)
        if err:
            return SimpleResponse(err, parse_mode="HTML")
        if matches:
            return StationChoiceForSettingResponse(setting_type, query, matches)

        codes = loadStationCodes()
        names = [n.title() for n, c in codes.items() if c == code]
        name = names[0] if names else code

        if setting_type == 'home':
            saveUserCommute(self.user_id, home=code, home_name=name)
        else:
            saveUserCommute(self.user_id, work=code, work_name=name)

        label = "Home 🏠" if setting_type == 'home' else "Work 🏢"
        msg = f"✅ Saved <b>{html.escape(name)} ({code})</b> as your <b>{label}</b> station."

        # Check if both are now set
        settings = getUserSettings(self.user_id)
        if settings.get("home") and settings.get("work"):
            msg += "\n\n🚀 Both stations are set! You can now type <code>commute</code> anytime for live departures."

        return SimpleResponse(msg, parse_mode="HTML")

    def _handleCommute(self):
        settings = getUserSettings(self.user_id)
        home = settings.get("home")
        work = settings.get("work")

        if not home or not work:
            missing = []
            if not home:
                missing.append("• <code>set home &lt;station&gt;</code> (e.g. <code>set home dartford</code>)")
            if not work:
                missing.append("• <code>set work &lt;station&gt;</code> (e.g. <code>set work london bridge</code>)")
            msg = (
                "🚉 <b>Commute shortcuts are not fully set up yet.</b>\n\n"
                "Please configure your stations:\n" + "\n".join(missing) + "\n\n"
                "Once set, typing <code>commute</code> will automatically fetch your live train times!"
            )
            return SimpleResponse(msg, parse_mode="HTML")

        # Check for explicit direction: 'commute to work', 'commute work', 'commute to home', 'commute home'
        params_str = " ".join(self.requestParams[1:]).lower()
        if 'work' in params_str:
            from_crs, to_crs = home, work
        elif 'home' in params_str:
            from_crs, to_crs = work, home
        else:
            # Automatic time-of-day detection:
            # 04:00 - 12:59 -> Morning (Home to Work)
            # 13:00 - 03:59 -> Evening (Work to Home)
            now = datetime.datetime.now()
            if 4 <= now.hour < 13:
                from_crs, to_crs = home, work
            else:
                from_crs, to_crs = work, home

        return RailRequest.getDepartureForStation(from_crs, to_crs)
