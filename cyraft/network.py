import asyncio

from .log import logger
from .serializers import MessagePackSerializer

# from .conf import config


class UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, request_handler):  # serializer=None, cryptor=None):
        # self.queue = queue
        self.serializer = MessagePackSerializer()
        # self.serializer = serializer or config.serializer
        # self.cryptor = cryptor or config.cryptor
        self.request_handler = request_handler
        # self.loop = loop

    def __call__(self):
        return self

    # async def start(self):
    #     while not self.transport.is_closing():
    #         request = await self.queue.get()
    #         # data = self.cryptor.encrypt(self.serializer.pack(request["data"]))
    #         data = self.serializer.pack(request["data"])
    #         self.transport.sendto(data, request["destination"])

    def connection_made(self, transport):
        self.transport = transport
        # asyncio.ensure_future(self.start(), loop=self.loop)

    def datagram_received(self, data, sender):
        message = data.decode()
        logger.info(f"Received {message} from {sender}")
        self.request_handler(message)

    # def error_received(self, exc):
    #     logger.error("Error received {}".format(exc))

    # def connection_lost(self, exc):
    #     logger.error("Connection lost {}".format(exc))


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


async def _unittest_network_udp_protocol_receive() -> None:
    # Create a callback function
    receive_togle = False

    def receive_handler(data):
        nonlocal receive_togle
        receive_togle = True
        print(f"Received {data}")

    loop = asyncio.get_running_loop()

    # Create a queue and a protocol
    # queue = asyncio.Queue()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UDPProtocol(receive_handler),
        local_addr=("127.0.0.1", 9999),
    )

    # Send a message
    message = "Hello World!"
    transport.sendto(message.encode(), ("127.0.0.1", 9999))

    try:
        await asyncio.sleep(2)  # wait for 1 second
    finally:
        transport.close()

    assert receive_togle


# async def _unittest_network_udp_protocol_send() -> None:
