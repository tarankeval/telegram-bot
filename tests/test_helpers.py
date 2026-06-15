import unittest

from helpers import extract_prompt, recent_messages, wants_image


class HelperTests(unittest.TestCase):
    def test_image_intent_and_prompt_extraction(self):
        self.assertTrue(wants_image("Нарисуй лотос в горах"))
        self.assertEqual(extract_prompt("Нарисуй: лотос в горах"), "лотос в горах")
        self.assertFalse(wants_image("Как проходит твой день?"))

    def test_recent_messages_strips_extra_fields_and_limits_context(self):
        messages = [
            {"role": "user", "content": str(index), "time": "12:00:00"}
            for index in range(35)
        ]

        result = recent_messages(messages)

        self.assertEqual(len(result), 30)
        self.assertEqual(result[0], {"role": "user", "content": "5"})
        self.assertEqual(result[-1], {"role": "user", "content": "34"})


if __name__ == "__main__":
    unittest.main()
