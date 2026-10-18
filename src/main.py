#!/usr/bin/python3

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import req, rail, tfl, hello, util, nature, commute
from req import EmptyResponse
from tfl import TflRequest
from rail import RailRequest, StationCodeRequest
from fuel import FuelPriceRequest
from nature import NatureRequest
from commute import CommuteRequest, saveUserCommute, getUserSettings
from util import logging


def allRequestHandlers(messages, user_id=None):
    return [
        CommuteRequest(messages, user_id=user_id),
        TflRequest(messages),
        RailRequest(messages),
        StationCodeRequest(messages),
        FuelPriceRequest(messages),
        NatureRequest(messages),
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
    welcome_text = (
        "👋 <b>Welcome! I'm your UK travel & utility bot.</b>\n\n"
        "Here are some things I can do:\n"
        "• <code>commute</code> (or <code>c</code>) - Live train times for your daily commute\n"
        "• <code>train &lt;origin&gt; to &lt;dest&gt;</code> - Realtime train departures\n"
        "• <code>code &lt;station&gt;</code> - National Rail station CRS codes\n"
        "• <code>fuel &lt;postcode&gt;</code> - UK fuel prices near a postcode (Fuel Finder)\n"
        "• <code>nature</code> - Latest articles from Nature & NatGeo\n\n"
        "💡 <i>Tip: Set your commute with <code>set home &lt;station&gt;</code> and <code>set work &lt;station&gt;</code>!</i>"
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        parse_mode="HTML"
    )

async def handleCallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    logging.info(f"Callback query received: {data}")

    user_id = str(update.effective_user.id) if update.effective_user else str(update.effective_chat.id)

    if data.startswith("train:"):
        parts = data.split(":")
        if len(parts) >= 3:
            from_crs, to_crs = parts[1], parts[2]
            resp = RailRequest.getDepartureForStation(from_crs, to_crs)
            lines = resp.toTelegramString()
            message_text = "\n".join(lines) if isinstance(lines, list) else str(lines)
            try:
                await query.edit_message_text(
                    text=message_text,
                    reply_markup=resp.replyMarkup(),
                    parse_mode=resp.parseMode()
                )
            except Exception as e:
                logging.debug(f"Edit message exception on train callback: {e}")

    elif data.startswith("sethome:") or data.startswith("setwork:"):
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

    elif data == "commute:now":
        resp = CommuteRequest(["COMMUTE"], user_id=user_id).handleRequest()
        lines = resp.toTelegramString()
        message_text = "\n".join(lines) if isinstance(lines, list) else str(lines)
        try:
            await query.edit_message_text(
                text=message_text,
                reply_markup=resp.replyMarkup(),
                parse_mode=resp.parseMode()
            )
        except Exception as e:
            logging.debug(f"Edit message exception on commute callback: {e}")

async def handleMessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    logging.info('New request: ' + text)
    if text.lower() == "help":
        await context.bot.send_message(chat_id=update.effective_chat.id, text=buildHelpString())
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
        parse_mode=resp.parseMode()
    )

if __name__ == '__main__':
    token = util.getTokenFromFile('token.key')
    application = ApplicationBuilder().token(token).build()
    
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)
    application.add_handler(CallbackQueryHandler(handleCallback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handleMessage))
    
    application.run_polling()
