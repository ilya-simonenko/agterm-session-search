import importlib.util
import json
import os
import shutil
import tempfile
import time
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

    def test_parse_resumes_after_a_partly_written_line(self):
        self.search.parse_transcript(self.transcript)
        with open(self.transcript, "a") as f:
            f.write('{"type":"user","message":{"content":"half')
        self.assertNotIn("half written", self.search.parse_transcript(self.transcript)["prompts"])
        with open(self.transcript, "a") as f:
            f.write(' written"}}\n')
        parsed = self.search.parse_transcript(self.transcript)
        self.assertEqual(parsed["prompts"], ["why does the login test fail on CI?", "half written"])
        self.assertEqual(parsed["title"], "Flaky login test investigation")

    def test_parse_starts_over_when_the_file_shrinks(self):
        self.search.parse_transcript(self.transcript)
        with open(self.transcript, "w") as f:
            f.write('{"type":"user","message":{"content":"fresh start"}}\n')
        self.assertEqual(self.search.parse_transcript(self.transcript)["prompts"], ["fresh start"])

    def test_digest_keeps_the_newest_end(self):
        parsed = {"prompts": ["first", "second"], "replies": ["answer"]}
        self.assertEqual(self.search.digest(parsed, 14), "CLAUDE: answer")
        self.assertIn("USER: first", self.search.digest(parsed, 1000))


class AskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.search = load("session-search")
        self.search.CACHE = self.tmp
        self.search.scrollback = lambda sessions: {"S1": "$ make migrate\nApplying 0042_split_line_items OK"}
        self.prompt = os.path.join(self.tmp, "prompt.txt")
        fake = os.path.join(self.tmp, "claude")
        with open(fake, "w") as f:
            f.write(f'#!/bin/sh\ncat > {self.prompt}\necho \'{{"picks": [{{"n": 1, "why": "the login cookie race"}}, {{"n": 9}}]}}\'\n')
        os.chmod(fake, 0o755)
        self.search.CLAUDE = fake
        self.transcript = os.path.join(self.tmp, "conversation.jsonl")
        shutil.copy(FIXTURE, self.transcript)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def session(self, sid, name, path=None):
        claude = [{"path": path, "title": "Flaky login test investigation", "summary": "Fixes a cookie race."}] if path else []
        return {"kind": "open", "id": sid, "name": name, "context": "", "cwd": "/tmp/project", "status": "", "claude": claude}

    def test_terms_drop_stopwords_and_short_words(self):
        self.assertEqual(self.search.terms_of("Where did we fix the login.test on CI #412?"),
                         ["fix", "login.test", "412"])

    def test_excerpts_prefer_the_window_naming_most_terms(self):
        texts = ["cookie mentioned alone " + "x " * 400 + "the redirect sets the cookie late"]
        self.assertIn("redirect sets the cookie", self.search.excerpts(texts, ["cookie", "redirect"])[0])

    def test_ask_sends_index_and_full_transcript_excerpts_and_keeps_valid_picks(self):
        sessions = [self.session("S1", "orders"), self.session("S2", "login", self.transcript)]
        answer = self.search.ask("where did the redirect break the cookie", sessions)
        self.assertEqual(answer, {"picks": [{"i": 1, "why": "the login cookie race"}]})
        with open(self.prompt) as f:
            prompt = f.read()
        self.assertIn("#1 [open, idle] login | /tmp/project | Flaky login test investigation | Fixes a cookie race.", prompt)
        self.assertIn("#1:\n  - The test reads the cookie before the redirect sets it.", prompt)
        self.assertNotIn("#0:", prompt)

    def test_ask_reports_an_answer_without_json(self):
        with open(self.search.CLAUDE, "w") as f:
            f.write("#!/bin/sh\necho I could not decide\n")
        self.assertEqual(self.search.ask("cookie", [self.session("S1", "a")]), {"error": "I could not decide"})


class SessionEmojiTests(unittest.TestCase):
    def setUp(self):
        self.names = load("claude-session-emoji")
        self.tmp = tempfile.mkdtemp()
        self.names.EMOJIS = os.path.join(self.tmp, "emojis.json")
        self.names.SUMMARIES = os.path.join(self.tmp, "summaries.json")
        self.names.BG_DIR = os.path.join(self.tmp, "backgrounds")
        self.names.BACKGROUNDS = True
        self.calls = []
        self.names.ctl = lambda *args: self.calls.append(args) or ""
        self.names.render_background = lambda path, emoji, title: open(path, "w").close() or True
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

    def write(self, *entries):
        with open(self.transcript, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_pick_waits_for_the_first_reply_before_asking(self):
        self.write({"type": "user", "message": {"content": "review PR 412"}},
                   {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                                                   "input": {"command": "gh pr view 412"}}]}})
        self.assertIsNone(self.names.pick_emoji("Review PR 412", self.transcript, "c1", {}))
        self.assertEqual(self.prompts, [])

    def test_first_turn_shows_what_claude_read_and_stops_at_the_next_prompt(self):
        self.write({"type": "user", "message": {"content": "review PR 412"}},
                   {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                                                                   "input": {"command": "gh pr view 412"}}]}},
                   {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                             "content": "Add a cookie jar to the login client " * 3}]}},
                   {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
                                                             "is_error": True, "content": "x" * 100}]}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looks fine."}]}},
                   {"type": "user", "message": {"content": "now check the deploy"}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "Deploy is green."}]}})
        turn = self.names.first_turn(self.transcript)
        self.assertTrue(turn.startswith("USER: review PR 412"))
        self.assertIn('CLAUDE READ [Bash {"command": "gh pr view 412"}]\nAdd a cookie jar', turn)
        self.assertIn("CLAUDE: Looks fine.", turn)
        self.assertNotIn("xxxx", turn)
        self.assertNotIn("deploy", turn)

    def test_first_turn_spans_a_resent_prompt_and_injected_skill_text(self):
        self.write({"type": "user", "message": {"content": "review PR 412"}},
                   {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
                   {"type": "user", "message": {"content": "review PR 412"}},
                   {"type": "user", "message": {"content": [{"type": "text", "text": "Base directory for this skill: /x"}]}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looks fine."}]}})
        turn = self.names.first_turn(self.transcript)
        self.assertEqual(turn, "USER: review PR 412\nCLAUDE: Looks fine.")

    def test_first_turn_keeps_the_reply_when_reads_overflow(self):
        self.names.TURN_BUDGET = 2000
        reads = [{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                                           "content": "y" * 5000}]}} for i in range(10)]
        self.write({"type": "user", "message": {"content": "review PR 412"}}, *reads,
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looks fine."}]}})
        turn = self.names.first_turn(self.transcript)
        self.assertLessEqual(len(turn), 2000)
        self.assertTrue(turn.endswith("CLAUDE: Looks fine."))

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

    def test_pick_sees_the_repo_and_the_emojis_already_taken(self):
        with open(self.names.EMOJIS, "w") as f:
            json.dump({"c0": {"emoji": "🪙", "title": "Order book replay"}}, f)
        self.say(400)
        self.replies = ["🍪"]
        self.assertEqual(self.names.pick_emoji("Login cookie race", self.transcript, "c1", {}), "🍪")
        self.assertIn("the project repository", self.prompts[0])
        self.assertIn("🪙 Order book replay", self.prompts[0])

    def test_pick_is_kept_per_conversation_across_retitles(self):
        self.say(400)
        self.replies = ["🍪"]
        self.assertEqual(self.names.pick_emoji("Review PR 412", self.transcript, "c1", {}), "🍪")
        self.assertEqual(self.names.pick_emoji("Login cookie race", self.transcript, "c1", {}), "🍪")
        self.assertEqual(len(self.prompts), 1)

    def test_settle_waits_for_a_late_write(self):
        import threading
        def late():
            time.sleep(0.4)
            self.say(500)
        threading.Thread(target=late).start()
        start = time.time()
        self.names.settle(self.transcript, quiet=0.6, limit=5, poll=0.05)
        self.assertGreater(time.time() - start, 0.9)
        self.assertGreaterEqual(sum(map(len, self.names.search_module().parse_transcript(self.transcript)["replies"])), 500)

    def test_settle_gives_up_on_a_file_that_keeps_growing(self):
        import threading
        stop = threading.Event()
        def grow():
            while not stop.is_set():
                self.say(1)
                time.sleep(0.05)
        threading.Thread(target=grow).start()
        start = time.time()
        self.names.settle(self.transcript, quiet=0.5, limit=0.8, poll=0.05)
        stop.set()
        self.assertLess(time.time() - start, 1.2)

    def test_background_waits_for_the_emoji(self):
        state = {}
        self.names.set_background("s1", {}, state, "c1", "", "Review PR 412")
        self.assertEqual((self.calls, state), ([], {}))

    def test_background_is_set_once_per_emoji_and_title(self):
        state = {}
        self.names.set_background("s1", {}, state, "c1", "🍪", "Login cookie race")
        path = state["background"]
        self.assertTrue(path.startswith(self.names.BG_DIR))
        self.assertEqual(self.calls[0][:4], ("session", "background", "image", path))
        self.names.set_background("s1", {"background": {"imagePath": path}}, state, "c1", "🍪", "Login cookie race")
        self.assertEqual(len(self.calls), 1)
        self.names.set_background("s1", {"background": {"imagePath": path}}, state, "c1", "🍪", "Cookie race fix")
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(state["background"], path)

    def test_background_set_by_someone_else_is_kept(self):
        state = {}
        self.names.set_background("s1", {"background": {"imagePath": "/elsewhere/bg.png"}}, state, "c1", "🍪", "t")
        self.names.set_background("s1", {"background": {"kind": "color"}}, state, "c1", "🍪", "t")
        self.assertEqual((self.calls, state), ([], {}))

    def test_a_new_background_style_replaces_the_shown_one(self):
        self.names.STATE_DIR = os.path.join(self.tmp, "names")
        self.names.SEEN = os.path.join(self.tmp, "seen.json")
        with open(self.names.EMOJIS, "w") as f:
            json.dump({"c1": {"emoji": "🍪", "title": "t"}}, f)
        session = {"name": "", "cwd": "/x/repo"}
        self.names.current_session = lambda sid: session
        self.names.run("Stop", "s1", self.transcript, "c1")
        session["name"] = "🍪 Flaky login test investigation"
        session["background"] = {"imagePath": self.calls[-1][3]}
        before = len(self.calls)
        self.names.run("Stop", "s1", self.transcript, "c1")
        self.assertEqual(len(self.calls), before)
        self.names.BG_STYLE += 1
        self.names.run("Stop", "s1", self.transcript, "c1")
        self.assertEqual(self.calls[-1][:3], ("session", "background", "image"))
        self.assertNotEqual(self.calls[-1][3], session["background"]["imagePath"])

    def test_background_skipped_when_it_cannot_render(self):
        self.names.render_background = lambda path, emoji, title: False
        state = {}
        self.names.set_background("s1", {}, state, "c1", "🍪", "t")
        self.assertEqual((self.calls, state), ([], {}))


@unittest.skipUnless(importlib.util.find_spec("PIL") and os.path.exists("/System/Library/Fonts/Apple Color Emoji.ttc"),
                     "needs Pillow and the macOS emoji font")
class SessionBackgroundTests(unittest.TestCase):
    def test_renders_a_dark_frame_of_the_screen_size(self):
        bg = load("session-background")
        img = bg.render("🍪", "Login cookie race " * 10)
        self.assertEqual(img.size, (bg.WIDTH, bg.HEIGHT))
        self.assertLess(img.convert("L").resize((1, 1)).getpixel((0, 0)), 80)  # mean brightness

    def test_a_tall_emoji_fits_the_frame(self):
        bg = load("session-background")
        glyph = bg.emoji_image("🛡️")
        self.assertGreater(glyph.height, glyph.width)
        bg.render("🛡️")


if __name__ == "__main__":
    unittest.main()
