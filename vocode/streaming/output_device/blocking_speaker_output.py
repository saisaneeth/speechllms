import asyncio
import queue
from typing import Optional

import numpy as np
import sounddevice as sd

from vocode.streaming.models.audio import AudioEncoding
from vocode.streaming.output_device.base_output_device import BaseOutputDevice
from vocode.streaming.utils.worker import ThreadAsyncWorker


class BlockingSpeakerOutput(BaseOutputDevice, ThreadAsyncWorker):
    DEFAULT_SAMPLING_RATE = 44100

    def __init__(
        self,
        device_info: dict,
        sampling_rate: Optional[int] = None,
        audio_encoding: AudioEncoding = AudioEncoding.LINEAR16,
    ):
        self.device_info = device_info
        sampling_rate = sampling_rate or int(
            self.device_info.get("default_samplerate", self.DEFAULT_SAMPLING_RATE)
        )
        BaseOutputDevice.__init__(self, sampling_rate, audio_encoding)
        ThreadAsyncWorker.__init__(self)
        self.stream = sd.OutputStream(
            channels=1,
            samplerate=self.sampling_rate,
            dtype=np.int16,
            device=int(self.device_info["index"]),
        )
        self._ended = False
        self.consume_nonblocking(self.sampling_rate * b"\x00")
        self.stream.start()

    def start(self):
        ThreadAsyncWorker.start(self)

    def _run_loop(self):
        while not self._ended:
            try:
                chunk = self.input_janus_queue.sync_q.get(timeout=1)
                self.stream.write(np.frombuffer(chunk, dtype=np.int16))
            except queue.Empty:
                continue

    def consume_nonblocking(self, chunk):
        ThreadAsyncWorker.consume_nonblocking(self, chunk)

    def terminate(self):
        self._ended = True
        ThreadAsyncWorker.terminate(self)
        self.stream.close()

    @classmethod
    def from_default_device(
        cls,
        **kwargs,
    ):
        return cls(sd.query_devices(kind="output"), **kwargs)
