import json
import os
from astrbot.api import logger

class Storage:
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.persistence = self._load()

    def _load(self) -> dict:
        default_data = {"channels": {}}
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return default_data
        return default_data

    def save(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.persistence, f, indent=2)
            
    def get_channel_data(self, channel_name: str) -> dict:
        if channel_name not in self.persistence["channels"]:
            self.persistence["channels"][channel_name] = {"last_post_id": 0}
        return self.persistence["channels"][channel_name]
        
    def update_last_id(self, channel_name: str, last_id: int):
        if channel_name not in self.persistence["channels"]:
            self.persistence["channels"][channel_name] = {}
        self.persistence["channels"][channel_name]["last_post_id"] = last_id
        self.save()
