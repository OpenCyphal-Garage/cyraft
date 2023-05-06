import asyncio
import logging

ELECTION_TIMEOUT = 5

_logger = logging.getLogger(__name__)


class ElectionWatchdog:
    def __init__(self) -> None:
        self.closing = False
        self.election_timeout = ELECTION_TIMEOUT
        self.reset_election_timeout = False
        self.election_timeout_reached = False

    async def watchdog_timer(self, timeout):
        while not self.closing:
            await asyncio.sleep(timeout)
            # Check if I need to start election (if the timer has been reset in the meantime)
            if self.reset_election_timeout == True:
                self.reset_election_timeout = False
            else:
                self.on_election_timeout()

    def kick_election_timeout(self) -> None:
        self.reset_election_timeout = True
        _logger.debug("Kicking the election timeout watchdog")

    def on_election_timeout(self) -> None:
        _logger.debug("Election timeout reached!")
        self.election_timeout_reached = True

    async def run(self) -> None:
        _logger.debug("Starting election watchdog")
        election_watchdog = asyncio.create_task(self.watchdog_timer(self.election_timeout))
        while not self.closing:
            await asyncio.sleep(1)

        _logger.debug("Closing election watchdog")
        election_watchdog.cancel()


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


async def _unittest_election_watchdog() -> None:
    watchdog_node = ElectionWatchdog()
    asyncio.create_task(watchdog_node.run())

    await asyncio.sleep(ELECTION_TIMEOUT - 1)  # Election timeout should not be reached here

    watchdog_node.kick_election_timeout()  # Reset election timeout

    await asyncio.sleep(1)  # Election timeout should be reached here, but it should be reset above

    _logger.debug("==== Election timeout should not be reached above this line ====")
    assert watchdog_node.election_timeout_reached == False

    await asyncio.sleep(ELECTION_TIMEOUT)

    _logger.debug("==== Election timeout should be reached above this line ====")
    assert watchdog_node.election_timeout_reached == True

    # closing
    watchdog_node.closing = True
    await asyncio.sleep(1)
