# src/__main__.py

import asyncio
import logging

from src.bot import run

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot execution stopped.")
