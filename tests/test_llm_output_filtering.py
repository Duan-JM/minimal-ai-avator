import sys
import types
import unittest


if "openai" not in sys.modules:
    openai_module = types.ModuleType("openai")

    class OpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = None

    openai_module.OpenAI = OpenAI
    sys.modules["openai"] = openai_module

if "loguru" not in sys.modules:
    loguru_module = types.ModuleType("loguru")

    class DummyLogger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    loguru_module.logger = DummyLogger()
    sys.modules["loguru"] = loguru_module

if "src.basereal" not in sys.modules:
    basereal_module = types.ModuleType("src.basereal")

    class BaseReal:
        pass

    basereal_module.BaseReal = BaseReal
    sys.modules["src.basereal"] = basereal_module

import src.llm as llm


class FakeDelta:
    def __init__(self, content=None, reasoning_content=None):
        self.content = content
        self.reasoning_content = reasoning_content


class FakeChoice:
    def __init__(self, delta):
        self.delta = delta


class FakeChunk:
    def __init__(self, delta):
        self.choices = [FakeChoice(delta)]


class FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kwargs):
        return self._chunks


class FakeChat:
    def __init__(self, chunks):
        self.completions = FakeCompletions(chunks)


class FakeClient:
    def __init__(self, chunks):
        self.chat = FakeChat(chunks)


class FakeReal:
    def __init__(self):
        self.sessionid = "test-session"
        self.messages = []

    def put_msg_txt(self, msg):
        self.messages.append(msg)


class LLMOutputFilteringTests(unittest.TestCase):
    def setUp(self):
        self.original_client = llm.client
        llm.clear_conversation_history()

    def tearDown(self):
        llm.client = self.original_client
        llm.clear_conversation_history()

    def test_filters_reasoning_and_keeps_final_answer(self):
        chunks = [
            FakeChunk(FakeDelta(reasoning_content="用户只是打招呼，我需要简洁一点。")),
            FakeChunk(FakeDelta(content="好的，用户发来的是“hi”。")),
            FakeChunk(FakeDelta(content="我需要简单回答，保持简洁。")),
            FakeChunk(FakeDelta(content="\n\nHi!")),
        ]
        llm.client = FakeClient(chunks)
        real = FakeReal()

        llm.llm_response("hi", real)

        self.assertEqual(real.messages, ["Hi!"])
        history = list(llm.get_conversation_history("test-session"))
        self.assertEqual(history[-1]["content"], "Hi!")


if __name__ == "__main__":
    unittest.main()
