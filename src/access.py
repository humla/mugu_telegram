"""Allow-list: only the owner and the users they approve can use the bot.

res/access.json holds {"owner": "<id>", "allowed": {"<id>": "name"}, "requested": {"<id>": "name"}}.
A stranger's first message asks the owner for approval with Allow/Ignore buttons;
"requested" remembers who has asked so the owner is only asked once per person.
"""
import html
import json
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
    return data


def saveAccess(data):
    with open(ACCESS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def userLabel(user):
    return user.full_name + (f" (@{user.username})" if user.username else "")


def isOwner(user_id):
    return str(user_id) == loadAccess()["owner"]


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

    if not data["owner"]:
        await context.bot.send_message(chat_id=chat_id, text=(
            f"This bot has no owner set up yet. Your Telegram user ID is {user_id}: put "
            f'{{"owner": "{user_id}"}} in res/access.json and restart the bot.'))
        raise ApplicationHandlerStop

    if user_id in data["requested"]:
        await context.bot.send_message(chat_id=chat_id, text="🔒 This is a private bot. Your access request is still waiting for the owner.")
        raise ApplicationHandlerStop

    data["requested"][user_id] = userLabel(user)
    saveAccess(data)
    logging.info(f"Access requested by {userLabel(user)} ({user_id})")
    await context.bot.send_message(
        chat_id=int(data["owner"]),
        text=f"👋 <b>{html.escape(userLabel(user))}</b> (ID <code>{user_id}</code>) wants to use the bot.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Allow", callback_data=f"access:allow:{user_id}"),
            InlineKeyboardButton("❌ Ignore", callback_data=f"access:ignore:{user_id}"),
        ]]))
    await context.bot.send_message(chat_id=chat_id, text=(
        "🔒 This is a private bot. I've asked the owner to let you in; "
        "you'll get a message here once you're approved."))
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
    """Allow/Ignore buttons on an access request. Only the owner can press them."""
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
        text = (f"❌ Ignored <b>{html.escape(name)}</b> (ID <code>{user_id}</code>). They won't be asked about again; "
                f"send <code>allow {user_id}</code> to let them in later.")
    await query.edit_message_text(text=text, parse_mode="HTML")


async def handleAccessCommand(update, context, words):
    """Owner commands: users, allow <id>, block <id>. Returns True when the message was one of them."""
    if not words or words[0] not in ("users", "allow", "block") or not isOwner(update.effective_user.id):
        return False
    data = loadAccess()
    command, args = words[0], words[1:]
    if command == "users":
        lines = ["👥 <b>Allowed users</b>"]
        lines += [f"• {html.escape(name)} · <code>{uid}</code>" for uid, name in data["allowed"].items()] or ["<i>Only you so far.</i>"]
        if data["requested"]:
            lines += ["", "<b>Asked but not allowed</b>"]
            lines += [f"• {html.escape(name)} · <code>{uid}</code>" for uid, name in data["requested"].items()]
        lines += ["", "<i>allow &lt;id&gt; adds someone, block &lt;id&gt; removes them.</i>"]
        text = "\n".join(lines)
    elif len(args) != 1 or not args[0].isdigit():
        text = f"Usage: <code>{command} &lt;user id&gt;</code>. Send <code>users</code> to see IDs."
    elif args[0] == data["owner"]:
        text = "That's you, the owner."
    elif command == "allow":
        name = await allowUser(context, data, args[0])
        text = f"✅ Allowed <b>{html.escape(name)}</b> (ID <code>{args[0]}</code>)."
    elif args[0] not in data["allowed"]:
        text = f"<code>{args[0]}</code> is not on the allow-list."
    else:
        # Keep them under "requested" so a blocked user cannot keep re-asking.
        name = data["allowed"].pop(args[0])
        data["requested"][args[0]] = name
        saveAccess(data)
        text = f"🚫 Removed <b>{html.escape(name)}</b> (ID <code>{args[0]}</code>)."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="HTML")
    return True


def ownerHelp(user_id):
    if not isOwner(user_id):
        return ""
    return "\n\nOwner only:\nusers : People allowed to use the bot and pending requests\nallow <id> : Let someone use the bot\nblock <id> : Remove someone's access"
