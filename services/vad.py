import audioop
import webrtcvad

# VAD config — matches insuranceCompany Agentic design
VAD_AGGRESSIVENESS = 3    # 0–3; 3 = most aggressive (best for noisy phone lines)
SAMPLE_RATE = 8000        # Hz (matches Twilio mulaw)
FRAME_MS = 20             # ms per VAD frame (10 | 20 | 30 ms supported)
FRAME_BYTES_PCM = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 16-bit PCM = 2 bytes/sample

# How many consecutive speech frames to trigger barge-in
# At 20ms per frame: 3 frames = 60ms of speech → barge-in fires
SPEECH_FRAMES_THRESHOLD = 3


class VADService:
    """
    WebRTC VAD wrapper for barge-in detection.
    Receives mulaw audio from Twilio, converts to PCM, runs VAD.
    Calls on_barge_in() when sustained speech is detected during TTS playback.
    """

    def __init__(self):
        self._vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self._buffer = b""
        self._speech_frame_count = 0
        self._barge_in_fired = False

    def reset(self) -> None:
        """Reset state — call when TTS finishes or barge-in fires."""
        self._buffer = b""
        self._speech_frame_count = 0
        self._barge_in_fired = False

    def process(self, mulaw_bytes: bytes) -> bool:
        """
        Process incoming mulaw audio bytes.
        Returns True if barge-in should fire (caller started speaking).
        """
        if self._barge_in_fired:
            return False

        # mulaw → 16-bit PCM (required by webrtcvad)
        pcm_bytes = audioop.ulaw2lin(mulaw_bytes, 2)
        self._buffer += pcm_bytes

        # Process complete VAD frames
        while len(self._buffer) >= FRAME_BYTES_PCM:
            frame = self._buffer[:FRAME_BYTES_PCM]
            self._buffer = self._buffer[FRAME_BYTES_PCM:]

            try:
                is_speech = self._vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                continue

            if is_speech:
                self._speech_frame_count += 1
                if self._speech_frame_count >= SPEECH_FRAMES_THRESHOLD:
                    self._barge_in_fired = True
                    return True
            else:
                # Reset consecutive count on silence
                self._speech_frame_count = max(0, self._speech_frame_count - 1)

        return False
