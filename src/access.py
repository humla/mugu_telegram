"""Allow-list: only the owner and the users they approve can use the bot.

res/access.json holds:
  {"owner": "<id>", "allowed": {"<id>": "name"}, "requested": {"<id>": "name"},
   "invite": "<code>"}

Strangers are queued silently under "requested": no notification is sent to the
owner, who can review the queue with /users at their leisure. The owner can also
share an invite link (t.me/<bot>?start=<code>) that lets someone in on first
contact without any approval step. "requested" also remembers who has already
been turned away so they only get one reply, not one per message.
"""
import html
import json
import os
import secrets

from telegram.ext import ApplicationHandlerStop

from util import logging

ACCESS_FILE = os.path.join(os.path.dirname(__file__), "../res/access.json")


def loadAccess():
    data = {}
    if os.path.exists(ACCESS_FILE):
        try:
            with open(ACCESS_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            logging.error(f"Could not read {ACCESS_FILE}: {e}")
    data["owner"] = str(data["owner"]) if data.get("owner") else None
    data.setdefault("allowed", {})
    data.setdefault("requested", {})
    data["invite"] = str(data["invite"]) if data.get("invite") else None
    return data


def saveAccess(data):
    with open(ACCESS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def userLabel(user):
    return user.full_name + (f" (@{user.username})" if user.username else "")


def isOwner(user_id):
    return str(user_id) == loadAccess()["owner"]


def newInviteCode():
    return secrets.token_urlsafe(16)


def inviteMatches(code, stored):
    """Constant-time comparison; the payload can be any text a user cares to type."""
    if not code or not stored:
        return False
    return secrets.compare_digest(code.encode(), stored.encode())


def startPayload(message):
    """The <code> in a /start <code> deep link, or None for a plain /start."""
    if not message or not message.text:
        return None
    words = message.text.split(maxsplit=1)
    if words[0].split("@")[0] != "/start" or len(words) < 2:
        return None
    return words[1].strip() or None


async def gate(update, context):
    """Runs before every other handler; stops updates from users who are not allowed."""
    user = update.effective_user
    if user is None:
        raise ApplicationHandlerStop
    user_id = str(user.id)
    data = loadAccess()
    if user_id == data["owner"] or user_id in data["allowed"]:
        return

    if update.callback_query:
        await update.callback_query.answer("🔒 You do not have access to this bot.")
        raise ApplicationHandlerStop
    if not update.message:
        raise ApplicationHandlerStop  # edits, live-location updates and the like: stay quiet
    chat_id = update.effective_chat.id

    code = startPayload(update.message)
    if code is not None:
        if inviteMatches(code, data["invite"]):
            data["allowed"][user_id] = userLabel(user)
            data["requested"].pop(user_id, None)
            saveAccess(data)
            logging.info(f"Invite accepted by {userLabel(user)} ({user_id})")
            context.user_data["invited"] = True
            return  # let the /start handler welcome them
        await context.bot.send_message(chat_id=chat_id, text=(
            "❌ That invite link isn't valid any more. Ask the owner for a fresh one."))
        raise ApplicationHandlerStop

    if not data["owner"]:
        await context.bot.send_message(chat_id=chat_id, text=(
            f"This bot has no owner set up yet. Your Telegram user ID is {user_id}: put "
            f'{{"owner": "{user_id}"}} in res/access.json and restart the bot.'))
        raise ApplicationHandlerStop

    if user_id in data["requested"]:
        raise ApplicationHandlerStop  # already turned away once: no need to reply again

    data["requested"][user_id] = userLabel(user)
    saveAccess(data)
    logging.info(f"Access requested by {userLabel(user)} ({user_id})")
    await context.bot.send_message(chat_id=chat_id, text=(
        "🔒 This is a private bot and access is invite-only. "
        "If you know the owner, ask them for an invite link."))
    raise ApplicationHandlerStop


async def allowUser(context, data, user_id):
    data["allowed"][user_id] = data["requested"].pop(user_id, None) or data["allowed"].get(user_id) or "unknown"
    saveAccess(data)
    try:
        await context.bot.send_message(chat_id=int(user_id), text="✅ You now have access to this bot. Type help to see what I can do.")
    except Exception as e:
        logging.info(f"Could not tell {user_id} they were allowed: {e}")
    return data["allowed"][user_id]


async def handleAccessCallback(update, context):
    """Allow/Ignore buttons on access requests sent before the silent queue existed."""
    query = update.callback_query
    _, action, user_id = query.data.split(":", 2)
    if not isOwner(update.effective_user.id):
        return
    data = loadAccess()
    name = data["requested"].get(user_id) or data["allowed"].get(user_id) or user_id
    if action == "allow":
        await allowUser(context, data, user_id)
        text = f"✅ Allowed <b>{html.escape(name)}</b> (ID <code>{user_id}</code>)."
    else:
        data["requested"].pop(user_id, None)
        saveAccess(data)
        text = (f"❌ Ignored <b>{html.escape(name)}</b> (ID <code>{user_id}</code>). "
                f"Send <code>allow {user_id}</code> to let them in later.")
    await query.edit_message_text(text=text, parse_mode="HTML")


async def inviteText(context, data, args):
    """Build the owner's reply for 'invite', 'invite new' and 'invite off'."""
    arg = args[0].lower() if args else ""
    if len(args) > 1 or arg not in ("", "new", "off"):
        return ("Usage: <code>invite</code> shows the link, <code>invite new</code> replaces it, "
                "<code>invite off</code> stops people joining.")
    if arg == "off":
        data["invite"] = None
        saveAccess(data)
        return ("🚫 Invite links are now disabled. Anyone who already joined keeps access; "
                "send <code>invite</code> to start a new one.")
    if arg == "new" or not data["invite"]:
        data["invite"] = newInviteCode()
    saveAccess(data)
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={data['invite']}"
    return (f"🔗 <b>Invite link</b>\n\n<a href=\"{link}\">{html.escape(link)}</a>\n\n"
            "Anyone who opens it gets in straight away, with no approval needed. "
            "Send <code>invite new</code> to replace it or <code>invite off</code> to disable it.")


async def handleAccessCommand(update, context, words):
    """Owner commands: users, allow/block/deny <id>, invite. Returns True when handled."""
    if not words:
        return False
    command = words[0].lower().lstrip("/").split("@")[0]
    if command not in ("users", "allow", "block", "deny", "invite"):
        return False
    if not isOwner(update.effective_user.id):
        return False
    data = loadAccess()
    args = words[1:]
    if command == "users":
        lines = ["👥 <b>Allowed users</b>"]
        lines += [f"• {html.escape(name)} · <code>{uid}</code>" for uid, name in data["allowed"].items()] or ["<i>Only you so far.</i>"]
        if data["requested"]:
            lines += ["", f"<b>Turned away ({len(data['requested'])})</b>"]
            lines += [f"• {html.escape(name)} · <code>{uid}</code>" for uid, name in data["requested"].items()]
        lines += ["", "<i>allow &lt;id&gt; adds someone, block &lt;id&gt; removes them, "
                       "deny &lt;id&gt; clears a turned-away entry.</i>"]
        text = "\n".join(lines)
    elif command == "invite":
        text = await inviteText(context, data, args)
    elif len(args) != 1 or not args[0].isdigit():
        text = f"Usage: <code>{command} &lt;user id&gt;</code>. Send <code>users</code> to see IDs."
    elif args[0] == data["owner"]:
        text = "That's you, the owner."
    elif command == "allow":
        name = await allowUser(context, data, args[0])
        text = f"✅ Allowed <b>{html.escape(name)}</b> (ID <code>{args[0]}</code>)."
    elif command == "deny":
        if args[0] in data["requested"]:
            name = data["requested"].pop(args[0])
            saveAccess(data)
            text = f"❌ Cleared the turned-away entry for <b>{html.escape(name)}</b> (ID <code>{args[0]}</code>)."
        elif args[0] in data["allowed"]:
            text = f"<code>{args[0]}</code> is allowed; use <code>block {args[0]}</code> to remove them."
        else:
            text = f"There was no turned-away entry for <code>{args[0]}</code>."
    elif args[0] not in data["allowed"]:
        text = f"<code>{args[0]}</code> is not on the allow-list."
    else:
        # Keep them under "requested" so they only get one "it's private" reply.
        name = data["allowed"].pop(args[0])
        data["requested"][args[0]] = name
        saveAccess(data)
        text = f"🚫 Removed <b>{html.escape(name)}</b> (ID <code>{args[0]}</code>)."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="HTML")
    return True


def ownerHelp(user_id):
    if not isOwner(user_id):
        return ""
    return ("\n\nOwner only:\n"
            "/users : People allowed to use the bot and who was turned away\n"
            "allow <id> : Let someone use the bot\n"
            "block <id> : Remove someone's access\n"
            "deny <id> : Clear a turned-away entry\n"
            "/invite : Shareable link that lets people in without approval\n"
            "invite new : Replace the link · invite off : Disable it")
