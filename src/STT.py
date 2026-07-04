import discord
import asyncio
import logging
import numpy as np
from scipy.signal import resample_poly
import discord.ext.voice_recv as voice_recv


class TranscriptSink(voice_recv.AudioSink):
    def __init__(self, bot_loop: asyncio.AbstractEventLoop, on_utterance):
        super().__init__()
        self.bot_loop = bot_loop
        self.on_utterance = on_utterance
        self.buffers: dict[int, bytearray] = {}

    def wants_opus(self) -> bool:
        return False  # decoded PCM, not raw Opus

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if user is None:
            return
        self.buffers.setdefault(user.id, bytearray()).extend(data.pcm)

    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_stop(self, member: discord.Member) -> None:
        pcm = self.buffers.pop(member.id, None)
        if not pcm:
            return
        asyncio.run_coroutine_threadsafe(
            self.on_utterance(member, bytes(pcm)),
            self.bot_loop,
        )

    def cleanup(self) -> None:
        self.buffers.clear()


def _transcribe_pcm(pcm: bytes, whisper_model) -> str:
    # 48kHz stereo int16 -> mono float32 -> 16kHz (exact 3:1 decimation)
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    audio = audio.reshape(-1, 2).mean(axis=1)
    audio = resample_poly(audio, up=1, down=3)
    segments, _ = whisper_model.transcribe(audio, language="en", vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()


def make_utterance_handler(client, active_sessions, whisper_model, speak_ai_response):
    async def on_voice_utterance(member: discord.Member, pcm: bytes):
        if len(pcm) < 48000 * 4 * 0.15:  # skip clips under ~150ms
            return

        text = await asyncio.to_thread(_transcribe_pcm, pcm, whisper_model)
        if not text:
            return

        logging.info(f"\n🎙️ {member}: {text}")

        voice_client = discord.utils.get(client.voice_clients, guild=member.guild) if member.guild else None
        if not voice_client:
            return

        user_id = str(member.id)
        session_key = f"voice:{voice_client.channel.id}:{user_id}"

        if session_key not in active_sessions:
            active_sessions[session_key] = {'buffer': [], 'task': None}

        session = active_sessions[session_key]
        session['buffer'].append(text)

        if session['task'] and not session['task'].done():
            session['task'].cancel()

        session['task'] = asyncio.create_task(
            speak_ai_response(session_key, user_id, voice_client)
        )

    return on_voice_utterance
