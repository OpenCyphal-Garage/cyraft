import asyncio
import logging
import typing

from .serializers import MessagePackSerializer

_logger = logging.getLogger(__name__)


class UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue, request_handler: typing.Callable):
        self.queue = queue
        self.serializer = MessagePackSerializer()
        self.request_handler = request_handler

    def __call__(self):
        return self

    async def start(self):
        while not self.transport.is_closing():
            request = await self.queue.get()
            data = self.serializer.pack(request["data"])
            self.transport.sendto(data, request["destination"])

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transport = transport
        loop = asyncio.get_running_loop()
        self.task = loop.create_task(self.start())

    def datagram_received(self, data: bytes, sender: typing.Tuple[str, int]):
        message = data.decode()
        _logger.debug(f"Received {message} from {sender}")
        self.request_handler(message)

    def close(self):
        self.task.cancel()
        self.transport.close()


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


async def _unittest_network_udp_protocol() -> None:

    # Create a callback function
    receive_toggle = False

    def receive_handler(data):
        nonlocal receive_toggle
        receive_toggle = True
        _logger.debug(f"Received {data}")

    loop = asyncio.get_running_loop()

    # Create a queue and a protocol
    queue = asyncio.Queue()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UDPProtocol(queue, receive_handler),
        local_addr=("127.0.0.1", 9999),
    )

    # Send a message
    message = "Hello World!"
    transport.sendto(message.encode(), ("127.0.0.1", 9999))

    try:
        await asyncio.sleep(2)  # wait for 2 seconds
    finally:
        protocol.close()

    assert receive_toggle
