import unittest

from utils import message_parser


class TestMessageParser(unittest.TestCase):
    def test_parse_message_with_general_comment(self):
        raw_message = (
            "{\n"
            "'Task A': {'priority_number': 1, 'priority_level': 'high', 'comment': 'Do now'}\n"
            "}\n\n"
            "General summary line."
        )
        parsed_json, general_message = message_parser.parse_chatgpt_message(
            raw_message, project_logger=_MockLogger()
        )
        self.assertEqual(parsed_json["Task A"]["priority_number"], 1)
        self.assertEqual(general_message, "General summary line.")

    def test_parse_message_without_general_comment(self):
        raw_message = (
            "{\n"
            "'Task B': {'priority_number': 2, 'priority_level': 'medium', 'comment': 'Plan'}\n"
            "}"
        )
        parsed_json, general_message = message_parser.parse_chatgpt_message(
            raw_message, project_logger=_MockLogger()
        )
        self.assertEqual(parsed_json["Task B"]["priority_level"], "medium")
        self.assertEqual(general_message, "")


class _MockLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None
