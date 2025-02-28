import json
import os
import datetime


DEFAULT = {
    "album_title": "",
    "album_id": "",
    "album_size": 0,
    "current_photo": "",
    "speed": 1800,
    "transition": "normal",
    "authed": False,
    "next_update": "1970-01-01 00:00:00.000000",
    "downloaded": False
}


class Store:
    def __init__(self):
        self.file = "store.json"

        # Create file if it doesn't exist
        if not os.path.exists(self.file):
            with open(self.file, 'w') as f:
                json.dump(DEFAULT, f, indent=2, default=str)
    
    def _write_all(self, data:dict):
        with open(self.file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        
    def _read_all(self) -> dict:
        with open(self.file, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return dict()
    
    def get(self, key, default=None):
        data = self._read_all()
        entry = data.get(key, default)

        # Try to return the correct type
        converters = [
            lambda x: datetime.datetime.strptime(x, r"%Y-%m-%d %H:%M:%S.%f"),
            lambda x: datetime.datetime.fromisoformat(x)
        ]

        for convert in converters:
            try: return convert(entry)
            except (ValueError, TypeError): pass

        
        return entry

    def set(self, key, value):
        data = self._read_all()
        data[key] = value
        self._write_all(data)


store = Store()
