import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TalkFrontendAudioTests(unittest.TestCase):
    def test_talk_page_exposes_audio_element(self):
        html = (ROOT / "frontend" / "static" / "talk.html").read_text(encoding="utf-8")
        self.assertIn('id="remoteAudio"', html)
        self.assertIn("<audio", html)

    def test_client_binds_and_plays_remote_audio(self):
        js = (ROOT / "frontend" / "static" / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("this.remoteAudio = document.getElementById('remoteAudio');", js)
        self.assertIn("event.track.kind === 'audio'", js)
        self.assertIn("this.remoteAudio.srcObject = stream;", js)
        self.assertIn("await this.remoteAudio.play()", js)

    def test_client_includes_remote_media_diagnostics(self):
        js = (ROOT / "frontend" / "static" / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("setupRemoteMediaDiagnostics()", js)
        self.assertIn("this.remoteAudio.addEventListener('play'", js)
        self.assertIn("远端音频元素事件", js)

    def test_client_shows_audio_unlock_fallback(self):
        js = (ROOT / "frontend" / "static" / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("showStartButton(message =", js)
        self.assertIn("this.showStartButton('点击开启声音')", js)
        self.assertIn("startChatBtn.addEventListener('click'", js)

    def test_client_logs_audio_levels(self):
        js = (ROOT / "frontend" / "static" / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("setupAudioLevelMonitor()", js)
        self.assertIn("createAnalyser()", js)
        self.assertIn("远端音频电平", js)


if __name__ == "__main__":
    unittest.main()
