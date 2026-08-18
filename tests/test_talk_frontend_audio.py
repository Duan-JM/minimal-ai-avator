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

    def test_client_does_not_run_high_frequency_audio_level_logging(self):
        js = (ROOT / "frontend" / "static" / "client_talk.js").read_text(encoding="utf-8")
        self.assertNotIn("setupAudioLevelMonitor()", js)
        self.assertNotIn("远端音频电平", js)

    def test_client_has_retry_and_complete_page_cleanup(self):
        html = (ROOT / "frontend" / "static" / "talk.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend" / "static" / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn('id="retryConnectionBtn"', html)
        self.assertIn("async retryConnection()", js)
        self.assertIn("this.remoteVideo.srcObject = null;", js)
        self.assertIn("this.remoteAudio.srcObject = null;", js)
        self.assertIn("window.addEventListener('pagehide'", js)
        self.assertIn("window.addEventListener('pageshow'", js)

    def test_unsupported_speech_recognition_is_actionable(self):
        js = (ROOT / "frontend" / "static" / "client_talk.js").read_text(encoding="utf-8")
        self.assertIn("this.speechRecognitionSupported = false;", js)
        self.assertIn("当前浏览器不支持语音识别", js)
        self.assertIn("NotAllowedError", js)


if __name__ == "__main__":
    unittest.main()
