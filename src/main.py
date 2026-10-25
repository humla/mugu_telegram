#!/usr/bin/python3

import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, TypeHandler, filters
import req, rail, tfl, hello, util, nature, commute
from req import EmptyResponse
from tfl import TflRequest
from rail import RailRequest, StationCodeRequest
from fuel import FuelPriceRequest, fuelPricesNear
from nature import NatureRequest
from commute import CommuteRequest, saveUserCommute, getUserSettings
from nearby import NearbyRequest, nearbyResponse, NEW_MESSAGE_PREFIX
from vehicle import VehicleRequest
from lookup import WikiRequest, DefineRequest, TimeRequest, NewsRequest
import access
from util import logging


def allRequestHandlers(messages, user_id=None):
    return [
        CommuteRequest(messages, user_id=user_id),
        TflRequest(messages),
        RailRequest(messages),
        StationCodeRequest(messages),
        FuelPriceRequest(messages),
        NatureRequest(messages),
        NearbyRequest(messages),
        VehicleRequest(messages),
        WikiRequest(messages),
        DefineRequest(messages),
        TimeRequest(messages),
        NewsRequest(messages),
    ]

def getResponseObject(messages, user_id=None):
    allRequests = allRequestHandlers(messages, user_id=user_id)
    for request_handler in allRequests:
        resp = request_handler.handleRequestBase()
        if resp is not None and not isinstance(resp, EmptyResponse):
            return resp
    return None

def buildResponse(messages, user_id=None):
    resp = getResponseObject(messages, user_id=user_id)
    if resp:
        first_response = resp.toTelegramString()
        if isinstance(first_response, list):
            return "\n".join(first_response)
        return str(first_response)
    return ""

def buildHelpString():
    allRequests = allRequestHandlers(["help"])
    responses = list(filter(lambda b: b is not None, (map(lambda a: a.helpString(), allRequests))))
    return "\n".join(responses)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    invited = context.user_data.pop("invited", False) if context.user_data is not None else False
    welcome_text = (
        ("✅ Your invite worked — welcome aboard!\n\n" if invited else "") +
        "👋 <b>Welcome! I'm your UK travel & utility bot.</b>\n\n"
        "Here are the things I can do:\n"
        f"{html.escape(buildHelpString(), quote=False)}\n\n"
        "💡 <i>Type <code>help</code> any time to see this list again.</i>"
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        parse_mode="HTML"
    )

def callbackResponse(data, user_id):
    """The board a button asks for, or None for unknown button data."""
    parts = data.split(":")
    if data.startswith("train:") and len(parts) >= 3:
        return RailRequest.getDepartureForStation(parts[1], parts[2])
    if data.startswith("tfl:") and len(parts) >= 3:
        return TflRequest.getArrivalsForStop(parts[1], parts[2])
    if data.startswith("fuel:") and len(parts) >= 3:
        return fuelPricesNear(float(parts[1]), float(parts[2]), "your location")
    if data == "commute:now":
        return CommuteRequest(["COMMUTE"], user_id=user_id).handleRequest()
    return None

async def handleCallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    logging.info(f"Callback query received: {data}")

    user_id = str(update.effective_user.id) if update.effective_user else str(update.effective_chat.id)

    if data.startswith("access:"):
        await access.handleAccessCallback(update, context)
        return

    if data.startswith("sethome:") or data.startswith("setwork:"):
        setting_type = "home" if data.startswith("sethome:") else "work"
        parts = data.split(":", 2)
        if len(parts) >= 3:
            code, name = parts[1], parts[2]
            if setting_type == "home":
                saveUserCommute(user_id, home=code, home_name=name)
            else:
                saveUserCommute(user_id, work=code, work_name=name)

            label = "Home 🏠" if setting_type == "home" else "Work 🏢"
            confirmation = f"✅ Saved <b>{name} ({code})</b> as your <b>{label}</b> station."

            # Check if commute is fully ready
            settings = getUserSettings(user_id)
            if settings.get("home") and settings.get("work"):
                confirmation += "\n\n🚀 Both stations are set! Tap below to check your live commute."
                reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🚆 Check Commute", callback_data="commute:now")]])
            else:
                reply_markup = None

            try:
                await query.edit_message_text(
                    text=confirmation,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.debug(f"Edit message exception on set station callback: {e}")
        return

    # Buttons on the nearby board open a new message so the board stays put;
    # every other button replaces the message it is attached to.
    new_message = data.startswith(NEW_MESSAGE_PREFIX)
    if new_message:
        data = data[len(NEW_MESSAGE_PREFIX):]
    resp = callbackResponse(data, user_id)
    if resp is None:
        return

    lines = resp.toTelegramString()
    message_text = "\n".join(lines) if isinstance(lines, list) else str(lines)
    if new_message:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message_text,
            reply_markup=resp.replyMarkup(),
            parse_mode=resp.parseMode()
        )
        return
    try:
        await query.edit_message_text(
            text=message_text,
            reply_markup=resp.replyMarkup(),
            parse_mode=resp.parseMode()
        )
    except Exception as e:
        logging.debug(f"Edit message exception on {data} callback: {e}")

async def handleUsersCommand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Slash-command form of the owner-only 'users' allow-list listing."""
    await access.handleAccessCommand(update, context, ["users"])

async def handleInviteCommand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: show or rotate the invite link."""
    await access.handleAccessCommand(update, context, update.message.text.lower().split())

async def handleLocation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = update.message.location
    resp = nearbyResponse(location.latitude, location.longitude)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(resp.toTelegramString()),
        reply_markup=resp.replyMarkup(),
        parse_mode=resp.parseMode()
    )

async def handleMessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    logging.info('New request: ' + text)
    if text.lower() == "help":
        help_text = buildHelpString() + access.ownerHelp(update.effective_user.id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=help_text)
        return
    if await access.handleAccessCommand(update, context, text.lower().split()):
        return

    user_id = str(update.effective_user.id) if update.effective_user else str(update.effective_chat.id)
    messages = list(map(lambda w: w.upper(), text.split()))
    resp = getResponseObject(messages, user_id=user_id)
    if not resp:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Sorry I do not understand that message yet. Pls type help to get a list of commands i understand."
        )
        return

    lines = resp.toTelegramString()
    message_text = "\n".join(lines) if isinstance(lines, list) else str(lines)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message_text,
        reply_markup=resp.replyMarkup(),
        parse_mode=resp.parseMode(),
        link_preview_options=LinkPreviewOptions(is_disabled=True) if resp.disableLinkPreview() else None
    )

def buildApplication(token):
    application = ApplicationBuilder().token(token).build()

    # Group -1 runs before every other handler and stops updates from users not on the allow-list.
    application.add_handler(TypeHandler(Update, access.gate), group=-1)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('users', handleUsersCommand))
    application.add_handler(CommandHandler('invite', handleInviteCommand))
    application.add_handler(CallbackQueryHandler(handleCallback))
    # New location messages only: a live location also sends an edited message
    # every few seconds, which would otherwise repost the board each time.
    application.add_handler(MessageHandler(filters.LOCATION & filters.UpdateType.MESSAGE, handleLocation))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handleMessage))
    return application

if __name__ == '__main__':
    buildApplication(util.getTokenFromFile('token.key')).run_polling()
