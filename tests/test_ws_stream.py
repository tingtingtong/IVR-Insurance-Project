"""
WebSocket stream simulator — replays a WAV file as Twilio Media Stream events.
Tests the full pipeline: audio → Deepgram STT → LangGraph → ElevenLabs TTS.

Usage:
  python tests/test_ws_stream.py                    # uses built-in silence/tone
  python tests/test_ws_stream.py path/to/speech.wav # replay a real recording

Requires:
  - main app running: uvicorn main:app --port 8000
  - .env with all API keys set
"""
import asyncio
import base64
import json
import sys
import audioop
import struct
import os
import wave

import websockets

WS_URL = "ws://localhost:8888/stream"
FAKE_CALL_SID   = "CAtest00000000000000000000000001"
FAKE_STREAM_SID = "MZtest00000000000000000000000001"

CHUNK_MS    = 20          # Twilio sends 20ms chunks
SAMPLE_RATE = 8000
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_MS / 1000)  # 160 samples per chunk
CHUNK_BYTES   = CHUNK_SAMPLES * 2                    # 16-bit PCM = 2 bytes/sample


def _silence_mulaw(n_chunks: int) -> list[bytes]:
    """Generate n chunks of silence (mulaw encoded)."""
    silence_pcm = b"\x00" * CHUNK_BYTES
    silence_mulaw = audioop.lin2ulaw(silence_pcm, 2)
    return [silence_mulaw] * n_chunks


def _wav_to_mulaw_chunks(wav_path: str) -> list[bytes]:
    """Read a WAV file and return list of 20ms mulaw chunks at 8kHz."""
    with wave.open(wav_path, "rb") as wf:
        n_channels  = wf.getnchannels()
        sampwidth   = wf.getsampwidth()
        framerate   = wf.getframerate()
        n_frames    = wf.getnframes()
        raw_pcm     = wf.readframes(n_frames)

    # Convert to mono 16-bit if needed
    if n_channels == 2:
        raw_pcm = audioop.tomono(raw_pcm, sampwidth, 0.5, 0.5)
    if sampwidth != 2:
        raw_pcm = audioop.lin2lin(raw_pcm, sampwidth, 2)

    # Resample to 8kHz if needed
    if framerate != SAMPLE_RATE:
        raw_pcm, _ = audioop.ratecv(raw_pcm, 2, 1, framerate, SAMPLE_RATE, None)

    # Split into 20ms chunks and convert to mulaw
    chunks = []
    for i in range(0, len(raw_pcm), CHUNK_BYTES):
        pcm_chunk = raw_pcm[i:i + CHUNK_BYTES]
        if len(pcm_chunk) < CHUNK_BYTES:
            pcm_chunk = pcm_chunk.ljust(CHUNK_BYTES, b"\x00")
        chunks.append(audioop.lin2ulaw(pcm_chunk, 2))

    return chunks


async def simulate_call(audio_chunks: list[bytes]):
    print(f"Connecting to {WS_URL} ...")

    async with websockets.connect(WS_URL) as ws:
        # ── Send 'start' event ─────────────────────────────────────────────
        await ws.send(json.dumps({
            "event":     "start",
            "streamSid": FAKE_STREAM_SID,
            "start": {
                "callSid":        FAKE_CALL_SID,
                "accountSid":     "ACtest",
                "streamSid":      FAKE_STREAM_SID,
                "mediaFormat":    {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                "customParameters": {
                    "callSid":    FAKE_CALL_SID,
                    "fromNumber": "+15551234567",
                },
            },
        }))
        print("Sent: start event")

        # ── Wait for greeting TTS ──────────────────────────────────────────
        await asyncio.sleep(3)

        # ── Stream audio chunks ────────────────────────────────────────────
        print(f"Streaming {len(audio_chunks)} audio chunks ({len(audio_chunks) * CHUNK_MS}ms) ...")
        for i, chunk in enumerate(audio_chunks):
            await ws.send(json.dumps({
                "event":     "media",
                "streamSid": FAKE_STREAM_SID,
                "media": {
                    "track":     "inbound",
                    "chunk":     str(i),
                    "timestamp": str(i * CHUNK_MS),
                    "payload":   base64.b64encode(chunk).decode("utf-8"),
                },
            }))
            await asyncio.sleep(CHUNK_MS / 1000)  # real-time pacing

        # ── Wait for IVR response ──────────────────────────────────────────
        print("Audio sent. Waiting for IVR response ...")
        await asyncio.sleep(5)

        # ── Listen for outbound media events ──────────────────────────────
        try:
            async with asyncio.timeout(10):
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    event = msg.get("event")
                    if event == "media":
                        payload = msg.get("media", {}).get("payload", "")
                        audio = base64.b64decode(payload)
                        print(f"  Received TTS audio chunk: {len(audio)} bytes")
                    elif event == "clear":
                        print("  Received: clear (barge-in or end of TTS)")
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass

        # ── Send stop ─────────────────────────────────────────────────────
        await ws.send(json.dumps({"event": "stop", "streamSid": FAKE_STREAM_SID}))
        print("Sent: stop event. Test complete.")


async def main():
    wav_path = sys.argv[1] if len(sys.argv) > 1 else None

    if wav_path and os.path.exists(wav_path):
        print(f"Loading WAV: {wav_path}")
        chunks = _wav_to_mulaw_chunks(wav_path)
    else:
        print("No WAV provided — sending 3 seconds of silence")
        chunks = _silence_mulaw(n_chunks=150)   # 150 × 20ms = 3s

    await simulate_call(chunks)


if __name__ == "__main__":
    asyncio.run(main())
