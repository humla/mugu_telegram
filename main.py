#!/usr/bin/python3

import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me!")

async def handleMessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    message.split('')
    await context.bot.send_message(chat_id=update.effective_chat.id,text='You just sent me a message, I am handling it')

if __name__ == '__main__':
    with open('token.key') as f:
        lines = f.readlines()
    f.close()
    token = lines[0].strip()
    print(token)
    application = ApplicationBuilder().token(token).build()
    
    start_handler = CommandHandler('start', start)
    application.add_handler(start_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handleMessage))
    
    application.run_polling()

print("Ending the server now")


