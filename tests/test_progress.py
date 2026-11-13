import asyncio
import sys
import unittest
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter

from mushin.media import ProcessRunner
from mushin.media.progress import FFmpegProgressParser
from mushin.models.events import ExecutionEvent, NodeProgressEvent
from mushin.models.node import Node, NodeSpec, Workflow
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner


class EmptyConfig(BaseModel):
    pass


class ParserTests(unittest.TestCase):
    def test_fragmentation_throttle_end_and_monotonic_time(self):
        values = []
        now = [0.0]
        parser = FFmpegProgressParser(values.append, clock=lambda: now[0])
        for byte in b'out_time_us=1000000\nprogress=continue\n':
            parser.feed(bytes([byte]))
        parser.feed(b'out_time_ms=2000000\nprogress=continue\n')
        now[0] = 1.0
        parser.feed(b'out_time_us=500000\nprogress=continue\n')
        parser.feed(b'out_time_us=3000000\nprogress=end\n')
        parser.feed(b'out_time_us=4000000\nprogress=continue\n')
        self.assertEqual(values, [1.0, 1.0, 3.0])

    def test_invalid_unknown_and_oversized_records(self):
        values = []
        parser = FFmpegProgressParser(values.append, interval=0)
        parser.feed(b'x' * 100000)
        self.assertLessEqual(len(parser.buffer), 4096)
        parser.feed(b'\nout_time_us=N/A\nprogress=continue\n')
        for invalid in (b'nan', b'inf', b'-1', b'garbage'):
            parser.feed(b'out_time_us=' + invalid + b'\nprogress=continue\n')
        parser.feed(b'out_time_us=2500000\nprogress=end\n')
        self.assertEqual(values, [2.5])

    def test_event_roundtrip_and_unknown_percentage(self):
        event = NodeProgressEvent(run_id=uuid4(), node_id=uuid4(), completed=2)
        adapter = TypeAdapter(ExecutionEvent)
        self.assertEqual(adapter.validate_json(adapter.dump_json(event)), event)
        self.assertIsNone(event.percentage)
        self.assertEqual(event.model_copy(update={'total': 4}).percentage, 50)
        self.assertEqual(event.model_copy(update={'total': 1}).percentage, 100)


class ProgressExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_is_not_limited_by_stdout_capture(self):
        values = []
        parser = FFmpegProgressParser(values.append)
        result = await ProcessRunner(stdout_limit=1).run(
            sys.executable, ['-c', "import sys; sys.stdout.write('out_time_us=1000000\\nprogress=end\\n')"],
            on_stdout=parser.feed, timeout=5)
        self.assertEqual(values, [1])
        self.assertEqual(result.stdout, b'')

    async def test_cancellation_stops_streaming_before_return(self):
        values = []
        cancellation = asyncio.Event()
        def observe(seconds):
            values.append(seconds)
            cancellation.set()
        parser = FFmpegProgressParser(observe, interval=0)
        script = (
            "import sys,time; "
            "print('out_time_us=1000000\\nprogress=continue', flush=True); "
            "time.sleep(10); print('out_time_us=2000000\\nprogress=end', flush=True)"
        )
        with self.assertRaises(asyncio.CancelledError):
            await ProcessRunner(grace_period=0.1).run(
                sys.executable, ['-c', script], cancellation=cancellation,
                on_stdout=parser.feed, timeout=5)
        self.assertEqual(values, [1])
        await asyncio.sleep(0)
        self.assertEqual(values, [1])

    async def test_ordering_and_late_reports_after_success_failure_and_cancel(self):
        for outcome in ('success', 'failure', 'cancel'):
            with self.subTest(outcome=outcome):
                saved = []
                cancellation = asyncio.Event()

                class Executor:
                    async def execute(self, inputs, config, context):
                        saved.append(context)
                        context.report_progress(1, 2)
                        if outcome == 'failure':
                            raise ValueError('failed')
                        if outcome == 'cancel':
                            cancellation.set()
                            context.report_progress(2, 2)
                            context.raise_if_cancelled()
                        return {}

                registry = NodeRegistry()
                spec = NodeSpec(type='test.progress', config_model=EmptyConfig)
                registry.register(spec, Executor())
                node = Node(type=spec.type)
                events = []
                await WorkflowRunner(registry).run(Workflow(name='progress', nodes=[node]),
                                                  cancellation=cancellation, on_event=events.append)
                progress = [i for i, event in enumerate(events) if isinstance(event, NodeProgressEvent)]
                self.assertEqual(len(progress), 1)
                self.assertEqual(events[progress[0] - 1].status.value, 'running')
                self.assertEqual(events[progress[0] + 1].type, 'node_state_changed')
                count = len(events)
                saved[0].report_progress(2, 2)
                self.assertEqual(len(events), count)
