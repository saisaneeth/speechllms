import asyncio
from typing import Union

from vocode.streaming.models.transcriber import TranscriberConfig
from vocode.streaming.utils.worker import AsyncWorker, ThreadAsyncWorker


class Transcription:
    def __init__(
        self,
        message: str,
        confidence: float,
        is_final: bool,
        is_interrupt: bool = False,
    ):
        self.message = message
        self.confidence = confidence
        self.is_final = is_final
        self.is_interrupt = is_interrupt

    def __str__(self):
        return f"Transcription({self.message}, {self.confidence}, {self.is_final})"


class BaseTranscriber:
    def __init__(self, transcriber_config: TranscriberConfig):
        self.transcriber_config = transcriber_config

    def get_transcriber_config(self) -> TranscriberConfig:
        return self.transcriber_config

    async def ready(self):
        return True


class BaseAsyncTranscriber(AsyncWorker, BaseTranscriber):
    def __init__(
        self,
        transcriber_config: TranscriberConfig,
    ):
        self.input_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.output_queue: asyncio.Queue[Transcription] = asyncio.Queue()
        self.transcriber_config = transcriber_config
        AsyncWorker.__init__(self, self.input_queue, self.output_queue)
        BaseTranscriber.__init__(self, transcriber_config)

    async def _run_loop(self):
        raise NotImplementedError

    def send_audio(self, chunk):
        self.send_nonblocking(chunk)

    def terminate(self):
        AsyncWorker.terminate(self)


class BaseThreadAsyncTranscriber(ThreadAsyncWorker, BaseTranscriber):
    def __init__(
        self,
        transcriber_config: TranscriberConfig,
    ):
        self.input_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.output_queue: asyncio.Queue[Transcription] = asyncio.Queue()
        ThreadAsyncWorker.__init__(self, self.input_queue, self.output_queue)
        BaseTranscriber.__init__(self, transcriber_config)

    def _run_loop(self):
        raise NotImplementedError

    def send_audio(self, chunk):
        self.send_nonblocking(chunk)

    def terminate(self):
        ThreadAsyncWorker.terminate(self)
