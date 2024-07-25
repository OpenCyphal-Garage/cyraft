import asyncio
import logging

ELECTION_TIMEOUT = 5

_logger = logging.getLogger(__name__)


class ElectionWatchdog:
    def __init__(self) -> None:
        self.election_timeout = ELECTION_TIMEOUT
        self._next_election_timeout: float
        self._election_timer: asyncio.TimerHandle
        self.election_timeout_reached: bool = False

    def reset_election_timeout(self) -> None:
        _logger.debug("Reseting the election timer")
        loop = asyncio.get_event_loop()
        self._next_election_timeout = loop.time() + self.election_timeout
        self._election_timer.cancel()
        self._election_timer = loop.call_at(
            self._next_election_timeout, self.on_election_timeout
        )

    def on_election_timeout(self) -> None:
        _logger.debug("Election timeout reached!")
        self.election_timeout_reached = True
        self.reset_election_timeout()

    async def run(self) -> None:
        _logger.debug("Starting election watchdog")
        loop = asyncio.get_event_loop()
        self._next_election_timeout = loop.time() + self.election_timeout
        self._election_timer = loop.call_at(
            self._next_election_timeout, self.on_election_timeout
        )

    def close(self) -> None:
        _logger.debug("Closing election watchdog")
        self._election_timer.cancel()


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


async def _unittest_election_watchdog() -> None:
    watchdog_node = ElectionWatchdog()
    asyncio.create_task(watchdog_node.run())

    await asyncio.sleep(
        ELECTION_TIMEOUT - 1
    )  # Election timeout should not be reached here

    watchdog_node.reset_election_timeout()  # Reset election timeout

    await asyncio.sleep(
        1
    )  # Election timeout should be reached here, but it should be reset above

    _logger.debug("==== Election timeout should not be reached above this line ====")
    assert watchdog_node.election_timeout_reached is False

    await asyncio.sleep(ELECTION_TIMEOUT)

    _logger.debug("==== Election timeout should be reached above this line ====")
    assert watchdog_node.election_timeout_reached is True

    # closing
    watchdog_node.close()
    await asyncio.sleep(1)

    assert False
