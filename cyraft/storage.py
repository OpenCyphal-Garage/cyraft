import os

from .serializers import MessagePackSerializer


class FileDict:
    """Persistent dict-like storage on a disk accessible by obj['item_name']"""

    def __init__(self, filename, serializer=None):
        self.filename = filename
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)

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


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


def _unittest_storage_filedict() -> None:
    # Create a filedict
    filename = "~/cyraft/test_filedict"
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

    # Check that it raises KeyError
    try:
        filedict["test_non_existent"]
    except KeyError:
        pass
