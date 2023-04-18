#!/usr/bin/env python3
# Distributed under CC0 1.0 Universal (CC0 1.0) Public Domain Dedication.
# pylint: disable=ungrouped-imports,wrong-import-position

import sys
import asyncio
import logging

from raft_node import RaftNode

_logger = logging.getLogger(__name__)


async def main() -> None:
    logging.root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    _logger.addHandler(handler)
    logging.info("Starting the application...")
    app = RaftNode()
    try:
        await app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()


if __name__ == "__main__":
    asyncio.run(main())
