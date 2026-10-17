#!/usr/bin/python3

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import req, rail, tfl, hello, util, nature
from req import EmptyResponse
from tfl import TflRequest
from rail import RailRequest, StationCodeRequest
from fuel import FuelPriceRequest
from nature import NatureRequest
from util import logging


def allRequestHandlers(messages):
    return [TflRequest(messages), RailRequest(messages), StationCodeRequest(messages), FuelPriceRequest(messages), NatureRequest(messages)]

def getResponseObject(messages):
    allRequests = allRequestHandlers(messages)
    for request_handler in allRequests:
        resp = request_handler.handleRequestBase()
        if resp is not None and not isinstance(resp, EmptyResponse):
            return resp
    return None

def buildResponse(messages):
    resp = getResponseObject(messages)
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
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me!")

async def handleCallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    logging.info(f"Callback query received: {data}")

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
                logging.debug(f"Edit message exception (likely unchanged content): {e}")

async def handleMessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    logging.info('New request: ' + text)
    if text.lower() == "help":
        await context.bot.send_message(chat_id=update.effective_chat.id, text=buildHelpString())
        return

    messages = list(map(lambda w: w.upper(), text.split()))
    resp = getResponseObject(messages)
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
