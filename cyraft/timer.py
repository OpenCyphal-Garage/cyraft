import asyncio


class Timer:
    """Scheduling periodic callbacks"""

    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.loop = asyncio.get_running_loop()
        self.is_active = False

    def start(self):
        self.is_active = True
        self.handler = self.loop.call_later(self.get_interval(), self._run)

    def _run(self):
        if self.is_active:
            self.callback()
            self.handler = self.loop.call_later(self.get_interval(), self._run)

    def stop(self):
        self.is_active = False
        self.handler.cancel()

    def reset(self):
        self.stop()
        self.start()

    def get_interval(self):
        return self.interval


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


async def _unittest_timer() -> None:
    import time

    # Create a callback function
    callback_toggle = False

    def callback():
        nonlocal callback_toggle
        callback_toggle = True

    # Create a timer
    timer = Timer(0.1, callback)

    start_time = time.time()

    timer.start()
    await asyncio.sleep(1)
    timer.stop()
    assert callback_toggle

    end_time = time.time()

    assert 0.9 <= end_time - start_time <= 1.1
