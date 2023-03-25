import os

from .util.serializers import MessagePackSerializer


class FileDict:
    """Persistent dict-like storage on a disk accessible by obj['item_name']"""

    def __init__(self, filename: str, serializer=None):
        self.filename = os.path.join(
            "logs/", "{}.log".format(filename.replace(":", "_"))
        )
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        open(self.filename, "a").close()

        self.cache = {}
        self.serializer = MessagePackSerializer() if serializer is None else serializer

    def update(self, kwargs):
        for (key, value) in kwargs.items():
            self[key] = value

    def exists(self, name):
        try:
            self[name]
            return True
        except KeyError:
            return False

    def __getitem__(self, name):
        if name not in self.cache:
            try:
                content = self._get_file_content()
                if name not in content:
                    raise KeyError(f"Item {name} not found")
            except FileNotFoundError:
                open(self.filename, "wb").close()
                raise KeyError(f"File {self.filename} not found")

            else:
                self.cache = content
        return self.cache[name]

    def __setitem__(self, name, value):
        try:
            content = self._get_file_content()
        except FileNotFoundError:
            content = {}

        content.update({name: value})
        with open(self.filename, "wb") as f:
            f.write(self.serializer.pack(content))

        self.cache = content

    def __delitem__(self, name):
        try:
            content = self._get_file_content()
        except FileNotFoundError:
            raise KeyError(f"File {self.filename} not found")

        if name not in content:
            raise KeyError(f"Item {name} not found")

        del content[name]
        with open(self.filename, "wb") as f:
            f.write(self.serializer.pack(content))

        self.cache = content

    def _get_file_content(self):
        with open(self.filename, "rb") as f:
            content = f.read()
            if not content:
                return {}
        return self.serializer.unpack(content)


class Log:
    """
    Persistent Raft Log on a disk
    Log entries:
        {term: <term>, command: <command>}
        {term: <term>, command: <command>}
        ...

    Entry index is a corresponding line number
    """

    UPDATE_CACHE_EVERY = 5

    def __init__(self, node_id, serializer=None):
        self.filename = os.path.join(
            "logs/", "{}.log".format(node_id.replace(":", "_"))
        )
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        open(self.filename, "a").close()

        self.serializer = MessagePackSerializer() if serializer is None else serializer
        self.cache = self.read()

        # All states

        """
        Volatile state on all servers: index of highest log entry known to be committed
        (initialiazed to 0, increases monotonically)
        """
        self.commit_index = 0

        """
        Volatile state on all servers: index of highest log entry applied to state machine
        (initiliazed to 0, increases monotonically)
        """
        self.last_applied = 0

        # Leaders

        """
        Volatile state on Leaders: for each server, index of the next log entry to send to that server
        (initialized to leader last log index + 1)
            {<follower>: index, ...}
        """
        self.next_index = None

        """
        Volatile state on Leaders: for each server, index of highest log entry known to be replicated on server
        (initialized to 0, increases monotonically)
            {<follower>: index, ...}
        """
        self.match_index = None

    def __getitem__(self, index):
        return self.cache[index - 1]

    def __bool__(self):
        return bool(self.cache)

    def __len__(self):
        return len(self.cache)

    def write(self, term, command):
        with open(self.filename, "ab") as f:
            entry = {"term": term, "command": command}
            f.write(self.serializer.pack(entry) + "\n".encode())

        self.cache.append(entry)
        if not len(self.cache) % self.UPDATE_CACHE_EVERY:
            self.cache = self.read()

        return entry

    def read(self):
        with open(self.filename, "rb") as f:
            return [self.serializer.unpack(entry) for entry in f.readlines()]

    def erase_from(self, index):
        updated = self.cache[: index - 1]
        open(self.filename, "wb").close()
        self.cache = []

        for entry in updated:
            self.write(entry["term"], entry["command"])

    @property
    def last_log_index(self):
        return len(self.cache)

    @property
    def last_log_term(self):
        if self.cache:
            return self.cache[-1]["term"]
        return 0


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


def _unittest_storage_filedict() -> None:
    # Create a filedict
    filename = "filedict"
    filedict = FileDict(filename=filename)

    # Check that it is empty
    assert filedict._get_file_content() == {}

    # Add an item
    filedict["test"] = 1

    # Check that it is not empty
    assert filedict.exists("test")

    # Check that it is equal to 1
    assert filedict["test"] == 1

    # Update the item
    filedict["test"] = 2

    # Check that it is equal to 2
    assert filedict["test"] == 2

    # Delete the item
    del filedict["test"]

    # Check that it is empty
    assert filedict._get_file_content() == {}

    # Check that it raises KeyError
    try:
        filedict["test_non_existent"]
    except KeyError:
        pass


def _unittest_storage_log() -> None:
    # Create a log
    log = Log(node_id="node_1")

    # Check that it is empty
    assert not log

    # Add an item
    log.write(term=1, command="command_1")

    # Check that it is not empty
    assert log

    # Check that it is equal to 1
    assert log[1] == {"term": 1, "command": "command_1"}

    # Update the item
    log.write(term=2, command="command_2")

    # Check that it is equal to 2
    assert log[2] == {"term": 2, "command": "command_2"}

    # Delete the items
    log.erase_from(index=2)
    log.erase_from(index=1)

    # Check that it is empty
    assert not log
