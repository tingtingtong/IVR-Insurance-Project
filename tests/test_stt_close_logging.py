"""#102: Deepgram's expected 'tasks cancelled' ERROR is dropped only while we are closing."""
import logging
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import services.stt as stt

SDK_LOGGER = "deepgram.clients.common.v1.abstract_async_websocket"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class StopLoggingExpectedCancelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cap = _Capture()
        self.lg = logging.getLogger(SDK_LOGGER)
        self.lg.addHandler(self.cap)

    def tearDown(self):
        self.lg.removeHandler(self.cap)

    async def test_cancel_error_during_close_is_not_logged(self):
        conn = MagicMock()

        async def finish():
            self.lg.error("tasks cancelled error: ")   # what the SDK does while we close

        conn.finish = finish
        await stt._finish_connection(conn)
        self.assertEqual(self.cap.messages, [])

    async def test_same_message_outside_a_close_is_still_logged(self):
        self.lg.error("tasks cancelled error: ")
        self.assertEqual(self.cap.messages, ["tasks cancelled error: "])

    async def test_other_errors_during_close_are_still_logged(self):
        conn = MagicMock()

        async def finish():
            self.lg.error("connection reset by peer")

        conn.finish = finish
        await stt._finish_connection(conn)
        self.assertEqual(self.cap.messages, ["connection reset by peer"])

    async def test_close_counter_is_restored_after_an_exception(self):
        conn = MagicMock()
        conn.finish = AsyncMock(side_effect=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            await stt._finish_connection(conn)
        self.assertEqual(stt._closing, 0)


if __name__ == "__main__":
    unittest.main()
