#!/usr/bin/python3

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import req, rail, tfl, hello, util
from tfl import TflRequest
from rail import RailRequest
from util import logging

def buildResponse(messages):
    allRequests = [TflRequest(messages), RailRequest(messages)]
    responses = list(filter(lambda b: b is not None, (map(lambda a: a.handleRequest(), allRequests))))
    first_response = responses[0].toTelegramString() if responses else ""
    return "\n".join(first_response)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me!")

async def handleMessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messages = list(map(lambda w: w.upper(), update.message.text.split()))
    logging.info('New request: ' + update.message.text)
    response = buildResponse(messages)
    await context.bot.send_message(chat_id=update.effective_chat.id,text=response)

if __name__ == '__main__':
    token = util.getTokenFromFile('token.key')
    application = ApplicationBuilder().token(token).build()
    
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handleMessage))
    
    application.run_polling()

logging.info("Ending the server now")

messages = ["train", "dfd"]
#response = buildResponse(messages)
#logging.info(response)
