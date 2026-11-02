"""FSB5 bank reading based on SatNav Voice Pack Creator's fsb5 helpers."""
import io
import os
import shutil
import struct
import wave
from dataclasses import dataclass

from . import config
from .assets import Reader

RATES = {0: 4000, 1: 8000, 2: 11000, 3: 11025, 4: 16000, 5: 22050,
         6: 24000, 7: 32000, 8: 44100, 9: 48000, 10: 96000}
CODECS = {1: "PCM8", 2: "PCM16", 3: "PCM24", 4: "PCM32", 5: "float PCM",
          6: "GCADPCM", 7: "IMA ADPCM", 8: "VAG", 9: "HEVAG", 10: "XMA",
          11: "MPEG", 12: "CELT", 13: "AT9", 14: "XWMA", 15: "Vorbis"}


@dataclass
class Sample:
    name: str
    rate: int
    channels: int
    frames: int
    mode: int
    data: bytes

    @property
    def seconds(self):
        return self.frames / self.rate


def parse_fsb(data):
    r = Reader(data)
    if r.take(4) != b"FSB5":
        raise ValueError("Not an FSB5 sample container")
    version, count, header_size, name_size, data_size, mode = r.unpack("6I")
    if version != 1:
        raise ValueError("Unsupported FSB5 version %d (expected 1)" % version)
    r.take(32)
    if count > header_size // 8 or 60 + header_size + name_size + data_size > len(data):
        raise ValueError("Invalid FSB5 section sizes")
    headers = Reader(r.take(header_size))
    names = r.take(name_size)
    audio = r.take(data_size)
    if names and len(names) < count * 4:
        raise ValueError("Truncated FSB5 name table")
    records = []
    for i in range(count):
        word = headers.unpack("Q")[0]
        rate = RATES.get((word >> 1) & 15)
        channels = ((word >> 5) & 3) + 1
        offset, frames = ((word >> 7) & 0x7FFFFFF) * 32, word >> 34
        more = word & 1
        while more:
            chunk = headers.u32()
            more = chunk & 1
            body = headers.take((chunk >> 1) & 0xFFFFFF)
            kind = chunk >> 25
            if kind == 1:
                if not body:
                    raise ValueError("Empty FSB5 channel override")
                channels = body[0]
            elif kind == 2:
                if len(body) != 4:
                    raise ValueError("Invalid FSB5 rate override")
                rate = struct.unpack("<I", body)[0]
        if not rate or not 1 <= channels <= 32:
            raise ValueError("Unsupported sample rate/channel header")
        if offset > len(audio) or (records and offset < records[-1][3]):
            raise ValueError("Invalid FSB5 sample data offset")
        name = "sample_%03d" % (i + 1)
        if names:
            pos = struct.unpack_from("<I", names, i * 4)[0]
            if pos < count * 4 or pos >= len(names):
                raise ValueError("Invalid FSB5 sample name offset")
            end = names.find(b"\0", pos)
            if end < 0:
                raise ValueError("Unterminated FSB5 sample name")
            name = names[pos:end].decode("utf-8", "replace")
        records.append((name, rate, channels, offset, frames))
    if headers.pos != len(headers.data):
        raise ValueError("FSB5 sample header size mismatch")
    return [Sample(name, rate, channels, frames, mode,
                   audio[offset:records[i + 1][3] if i + 1 < count else len(audio)])
            for i, (name, rate, channels, offset, frames) in enumerate(records)]


def read_bank(data):
    if data.startswith(b"FSB5"):
        return parse_fsb(data)
    if data[:4] != b"RIFF" or len(data) < 12:
        raise ValueError("Not a RIFF FMOD bank or FSB5 container")
    length = struct.unpack_from("<I", data, 4)[0] + 8
    if length > len(data):
        raise ValueError("Truncated FMOD bank")
    samples = []
    pos = 12
    while pos < length:
        if pos + 8 > length:
            raise ValueError("Truncated FMOD bank chunk header")
        tag, size = struct.unpack_from("<4sI", data, pos)
        start, end = pos + 8, pos + 8 + size
        if end > length:
            raise ValueError("Truncated FMOD bank chunk")
        if tag == b"SND ":
            fsb = data.find(b"FSB5", start, end)
            if fsb >= 0:
                samples.extend(parse_fsb(data[fsb:end]))
        pos = end
    if not samples:
        raise ValueError("This bank contains event data only; no embedded audio samples to play/export.")
    return samples


def pcm_wav(sample):
    if sample.mode != 2:
        raise ValueError("Expected PCM16 audio")
    expected = sample.frames * sample.channels * 2
    if expected > len(sample.data):
        raise ValueError("Truncated PCM16 sample")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(sample.channels)
        wav.setsampwidth(2)
        wav.setframerate(sample.rate)
        wav.writeframes(sample.data[:expected])
    return output.getvalue()


def xma_wav(sample):
    if sample.mode != 10 or sample.channels > 2:
        raise ValueError("XMA decoding currently supports mono/stereo samples only")
    block = len(sample.data)
    if not block:
        raise ValueError("Empty XMA sample")
    extra = struct.pack("<HIIIIIIIBBH", 1, 4 if sample.channels == 1 else 3, sample.frames,
                        block, 0, sample.frames, 0, 0, 0, 4, 1)
    fmt = struct.pack("<HHIIHHH", 0x166, sample.channels, sample.rate,
                      sample.rate * 2 * sample.channels, block if block <= 65535 else 2048,
                      16, len(extra)) + extra
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(sample.data)) + sample.data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def ffmpeg_path():
    bundled = os.path.join(config.ROOT, "bin", "ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError("XMA/MPEG playback requires bin\\ffmpeg.exe (or ffmpeg on PATH).")
