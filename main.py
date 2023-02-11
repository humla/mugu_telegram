#!/usr/bin/python3

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import req, rail, tfl, hello, util
from tfl import TflRequest
from rail import RailRequest
from util import logging

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me!")

async def handleMessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messages = list(map(lambda w: w.upper(), update.message.text.split()))
    allRequests = [TflRequest(messages), RailRequest(messages)]
    await context.bot.send_message(chat_id=update.effective_chat.id,text='You just sent me a message, I am handling it')

if __name__ == '__main__':
    token = util.getTokenFromFile('token.key')
    application = ApplicationBuilder().token(token).build()
    
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handleMessage))
    
    #application.run_polling()

logging.info("Ending the server now")

messages = ["train", "dfd"]
allRequests = [TflRequest(messages), RailRequest(messages)]

logging.info(list(map(lambda a: a.handleRequest(), allRequests)))
