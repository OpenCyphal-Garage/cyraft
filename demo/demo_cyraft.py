#!/usr/bin/env python3
# Distributed under CC0 1.0 Universal (CC0 1.0) Public Domain Dedication.
# pylint: disable=ungrouped-imports,wrong-import-position

import sys
import asyncio
import logging

import os
import sys

# Get the absolute path of the parent directory
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Add the parent directory to the Python path
sys.path.append(parent_dir)
# This can be removed if setting PYTHONPATH (export PYTHONPATH=cyraft)

from cyraft import RaftNode

_logger = logging.getLogger(__name__)


async def main() -> None:
    logging.root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    _logger.addHandler(handler)
    logging.info("Starting the application...")
    app = RaftNode()
    app.cluster = [app]
    try:
        await app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()


if __name__ == "__main__":
    asyncio.run(main())
