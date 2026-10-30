import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "conversation.jsonl")


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(ROOT, "bin", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.search = load("session-search")
        self.search.CACHE = self.tmp
        self.transcript = os.path.join(self.tmp, "conversation.jsonl")
        shutil.copy(FIXTURE, self.transcript)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_parse_keeps_real_prompts_and_reply_text_only(self):
        parsed = self.search.parse_transcript(self.transcript)
        self.assertEqual(parsed["prompts"], ["why does the login test fail on CI?"])
        self.assertEqual(parsed["replies"], ["The test reads the cookie before the redirect sets it."])
        self.assertEqual(parsed["title"], "Flaky login test investigation")
        self.assertEqual(parsed["cwd"], "/tmp/project")

    def test_parse_is_served_from_cache_until_the_file_changes(self):
        self.search.parse_transcript(self.transcript)
        with open(self.transcript, "a") as f:
            f.write('{"type":"user","message":{"content":"a later prompt"}}\n')
        self.assertIn("a later prompt", self.search.parse_transcript(self.transcript)["prompts"])

    def test_digest_keeps_the_newest_end(self):
        parsed = {"prompts": ["first", "second"], "replies": ["answer"]}
        self.assertEqual(self.search.digest(parsed, 14), "CLAUDE: answer")
        self.assertIn("USER: first", self.search.digest(parsed, 1000))


class EmojiNameTests(unittest.TestCase):
    def setUp(self):
        self.names = load("claude-emoji-name")
        self.tmp = tempfile.mkdtemp()
        self.names.EMOJIS = os.path.join(self.tmp, "emojis.json")
        self.names.SUMMARIES = os.path.join(self.tmp, "summaries.json")
        search = load("session-search")
        search.CACHE = self.tmp
        self.names.search_module = lambda: search
        self.prompts = []
        self.replies = []
        self.names.ask = lambda prompt: self.prompts.append(prompt) or self.replies.pop(0)
        self.transcript = os.path.join(self.tmp, "conversation.jsonl")
        shutil.copy(FIXTURE, self.transcript)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def say(self, n):
        with open(self.transcript, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": "x" * n}}) + "\n")

    def test_latest_title_wins(self):
        self.assertEqual(self.names.latest_title(FIXTURE), "Flaky login test investigation")

    def test_owned_accepts_claude_title_folder_and_own_name(self):
        owned = self.names.owned
        self.assertTrue(owned("✳ Flaky login test investigation", {}, ["Flaky login test investigation"], "/x/repo"))
        self.assertTrue(owned("repo", {}, ["t"], "/x/repo/"))
        self.assertTrue(owned("🚦 old", {"name": "🚦 old"}, ["new"], "/x/repo"))
        self.assertTrue(owned("", {}, ["t"], ""))

    def test_owned_refuses_a_manual_rename(self):
        self.assertFalse(self.names.owned("my api work", {"name": "🚦 old"}, ["Flaky login"], "/x/repo"))


    def test_pick_waits_for_replies_before_asking(self):
        self.assertIsNone(self.names.pick_emoji("Review PR 412", self.transcript, "c1", {}))
        self.assertEqual(self.prompts, [])

    def test_pick_waits_while_the_model_says_wait_then_forces_a_pick(self):
        state = {}
        self.say(400)
        self.replies = ["WAIT", "WAIT", "🍪"]
        self.assertIsNone(self.names.pick_emoji("Review PR 412", self.transcript, "c1", state))
        self.assertIsNone(self.names.pick_emoji("Review PR 412", self.transcript, "c1", state))
        self.assertEqual(len(self.prompts), 1)
        self.say(600)
        self.assertIsNone(self.names.pick_emoji("Review PR 412", self.transcript, "c1", state))
        self.say(600)
        self.assertEqual(self.names.pick_emoji("Review PR 412", self.transcript, "c1", state), "🍪")
        self.assertIn("reply WAIT", self.prompts[0])
        self.assertNotIn("reply WAIT", self.prompts[-1])
        self.assertIn("redirect sets it", self.prompts[-1])

    def test_pick_is_kept_per_conversation_across_retitles(self):
        self.say(400)
        self.replies = ["🍪"]
        self.assertEqual(self.names.pick_emoji("Review PR 412", self.transcript, "c1", {}), "🍪")
        self.assertEqual(self.names.pick_emoji("Login cookie race", self.transcript, "c1", {}), "🍪")
        self.assertEqual(len(self.prompts), 1)


if __name__ == "__main__":
    unittest.main()
