
import sys
import os
import unittest
from datetime import datetime, timezone

# Add current directory to sys.path so we can import modules
sys.path.append(os.getcwd())


# Imports moved to after patching


# Mock Telethon classes locally to avoid dependency issues in test env
class MessageMediaPhoto:
    pass

class MessageMediaDocument:
    def __init__(self):
        self.document = None

class DocumentAttributeFilename:
    def __init__(self, file_name):
        self.file_name = file_name

class MockMessage:
    def __init__(self, id, text="", date=None, media=None, entities=None):
        self.id = id
        self.text = text
        self.date = date or datetime.now(timezone.utc)
        self.media = media
        self.entities = entities or []
        self.grouped_id = None
        self.post = True # Simulate channel post

# Patch the imports in the actual modules if they import these classes
# But since Python imports are resolving at runtime and we are passing objects, 
# as long as isinstance checks work, it's fine. 
# However, the modules import these classes. 
# `from telethon.tl.types import Message`
# If we run this script, it imports `core.mergers.someacg`, which imports `telethon`.
# So we MUST mock `telethon` in sys.modules BEFORE importing core modules.

import sys
from unittest.mock import MagicMock


# Create a mock telethon module
mock_telethon = MagicMock()
mock_telethon.tl.types.Message = MockMessage
mock_telethon.tl.types.MessageMediaPhoto = MessageMediaPhoto
mock_telethon.tl.types.MessageMediaDocument = MessageMediaDocument
mock_telethon.tl.types.PeerUser = MagicMock()
sys.modules["telethon"] = mock_telethon
sys.modules["telethon.tl"] = mock_telethon.tl
sys.modules["telethon.tl.types"] = mock_telethon.tl.types

# Mock loguru
mock_loguru = MagicMock()
mock_loguru.logger = MagicMock()
sys.modules["loguru"] = mock_loguru


# Now we can safely import our modules (names are already imported at top, but we need to reload or just ensure this ran before? 
# The imports at the top `from core...` ran BEFORE this patching. 
# So `core.mergers.someacg` ALREADY tried to import telethon and failed?
# Rewriting this file to patch BEFORE imports.

from core.config.channel_config import ChannelConfigParser
from core.filters.message_filter import MessageFilter
from core.mergers.merger import MessageMerger

class TestArchitecture(unittest.TestCase):

    def test_config_parser(self):
        print("\n=== Test ChannelConfigParser ===")
        # 1. New Dictionary Format
        cfg_dict = {
            "channel_username": "TestChannel",
            "start_time": "2024-01-01",
            "check_interval": 100,
            "msg_limit": 50
        }
        config = ChannelConfigParser.parse(cfg_dict)
        self.assertEqual(config.channel_name, "TestChannel")
        self.assertEqual(config.interval, 100)
        self.assertEqual(config.msg_limit, 50)
        self.assertEqual(config.start_date.year, 2024)
        print("  [PASS] Dictionary format")

        # 2. Legacy String Format
        cfg_str = "LegacyChannel|2023-01-01|60|10"
        config = ChannelConfigParser.parse(cfg_str)
        self.assertEqual(config.channel_name, "LegacyChannel")
        self.assertEqual(config.interval, 60)
        self.assertEqual(config.msg_limit, 10)
        self.assertEqual(config.start_date.year, 2023)
        print("  [PASS] Legacy string format")
        
        # 3. Preset Override
        cfg_preset = {
            "channel_username": "PresetChannel",
            "config_preset": "2025-01-01|300|5"
        }
        config = ChannelConfigParser.parse(cfg_preset)
        self.assertEqual(config.channel_name, "PresetChannel")
        self.assertEqual(config.interval, 300)
        self.assertEqual(config.msg_limit, 5)
        self.assertEqual(config.start_date.year, 2025)
        print("  [PASS] Preset override")

    def test_message_filter(self):
        print("\n=== Test MessageFilter ===")
        config = {
            "filter_keywords": ["bad", "spam"],
            "filter_regex": r"^RegexMatch",
            "filter_hashtags": ["#ignore"]
        }
        f = MessageFilter(config)

        msgs = [
            ("Chan", MockMessage(1, "Hello World")),
            ("Chan", MockMessage(2, "This is bad content")),
            ("Chan", MockMessage(3, "RegexMatch start")),
            ("Chan", MockMessage(4, "Normal message")),
        ]
        
        filtered = f.filter_messages(msgs)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered[0][1].id, 1) # Hello World
        self.assertEqual(filtered[1][1].id, 4) # Normal message
        print("  [PASS] Keywords and Regex filtering")

    def test_message_merger_someacg(self):
        print("\n=== Test MessageMerger (SomeACG Rule) ===")
        config = {
            "merge_rules": [
                {
                    "channel": "SomeACG",
                    "rule_class": "SomeACGPreviewPlusOriginal",
                    "params": {"time_window_seconds": 10}
                }
            ]
        }
        merger = MessageMerger(config)

        # Construct SomeACG scenario
        # 1. Preview Msg (Photo + pixiv link)
        msg1 = MockMessage(100, text="Art by xxx https://www.pixiv.net/artworks/12345", date=datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc))
        msg1.media = MessageMediaPhoto() 

        # 2. Original File (Document + Filename matching ID)
        msg2 = MockMessage(101, text="", date=datetime(2024, 1, 1, 10, 0, 5, tzinfo=timezone.utc)) # 5s later
        doc = MessageMediaDocument()
        doc.document = type('obj', (object,), {'attributes': [DocumentAttributeFilename(file_name="12345_p0.jpg")]})()
        msg2.media = doc

        # 3. Unrelated message
        msg3 = MockMessage(102, text="Random", date=datetime(2024, 1, 1, 10, 0, 10, tzinfo=timezone.utc))

        messages = [
            ("SomeACG", msg1),
            ("SomeACG", msg2),
            ("SomeACG", msg3)
        ]

        merged = merger.merge_messages(messages)
        
        # Expect merged messages to have _merge_group_id
        self.assertEqual(len(merged), 3) # Count is same, but attributes added
        
        # Verify msg1 and msg2 have same merge group id
        self.assertTrue(hasattr(merged[0][1], "_merge_group_id"))
        self.assertTrue(hasattr(merged[1][1], "_merge_group_id"))
        self.assertEqual(merged[0][1]._merge_group_id, merged[1][1]._merge_group_id)
        
        # Verify msg3 does NOT have merge group id
        self.assertFalse(hasattr(merged[2][1], "_merge_group_id"))
        
        print("  [PASS] SomeACG preview+original merging")

if __name__ == '__main__':
    unittest.main()
