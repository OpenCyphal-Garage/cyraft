import msgpack


class MessagePackSerializer:
    @staticmethod
    def pack(data):
        return msgpack.packb(data, use_bin_type=True)

    @staticmethod
    def unpack(data):
        return msgpack.unpackb(data, use_list=True, raw=False)


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


def _unittest_serializer_message_pack() -> None:
    # test with data that is list
    data = [1, 2, 3, 4, 5]
    packed = MessagePackSerializer.pack(data)
    unpacked = MessagePackSerializer.unpack(packed)
    assert data == unpacked

    # test with data that is dict
    data = {"a": 1, "b": 2, "c": 3}
    packed = MessagePackSerializer.pack(data)
    unpacked = MessagePackSerializer.unpack(packed)
    assert data == unpacked
