import asyncio
import json
import os
import sys
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from consensus.kheavyhash import hash_to_target_int
from consensus.pow import compact_to_target, target_to_compact
from node.daemon import BTCKDaemon
from node.rpc_client import InProcessNodeRPCClient
from privacy.stealth_address import StealthKeyPair
from stratum.stratum_server import (
    BTCKStratumServer,
    CLIENT_JOB_HISTORY_LIMIT,
    StratumClient,
    diff_to_kaspa_target,
)


class MockWriter:
    def __init__(self):
        self.messages = []

    def write(self, data):
        self.messages.append(json.loads(data.decode()))

    async def drain(self):
        pass


class TestStratumTargetVerification(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.daemon = BTCKDaemon(host="127.0.0.1", network="simnet")
        self.writer = MockWriter()
        self.client = StratumClient(None, self.writer, "client-1")
        self.client.authorized = True
        self.client.worker_name = "worker"
        self.client.wallet_address = StealthKeyPair().get_public_stealth_address()
        self.server = BTCKStratumServer(
            network="simnet", node_rpc=InProcessNodeRPCClient(self.daemon)
        )
        self.server.clients[self.client.client_id] = self.client

    async def _job(self):
        return await self.server.create_new_block_job(self.client)

    async def _drain_block_submissions(self):
        if self.server._block_submission_tasks:
            await asyncio.gather(*tuple(self.server._block_submission_tasks))

    def test_client_job_history_covers_late_block_window(self):
        self.assertEqual(self.client.job_ids.maxlen, CLIENT_JOB_HISTORY_LIMIT)
        self.assertGreaterEqual(
            CLIENT_JOB_HISTORY_LIMIT,
            int(self.server.late_block_grace_seconds * 2),
        )

    def _response(self, message_id):
        return next(
            message
            for message in reversed(self.writer.messages)
            if message.get("id") == message_id and "result" in message
        )

    async def test_unknown_and_expired_jobs_are_rejected(self):
        await self.server.process_message(self.client, {"id": 1, "method": "mining.submit", "params": ["worker", "missing", "1"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 21)
        job = await self._job()
        job.created_at = time.time() - self.server.job_ttl_seconds - 1
        await self.server.process_message(self.client, {"id": 2, "method": "mining.submit", "params": ["worker", job.job_id, "1"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 21)

    async def test_low_difficulty_and_duplicate_shares(self):
        job = await self._job()
        values = [(n, hash_to_target_int(bytes.fromhex(job.build_block(n).hash))) for n in range(20)]
        nonce, value = max(values, key=lambda item: item[1])
        self.server.share_target = max(1, value - 1)
        await self.server.process_message(self.client, {"id": 3, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:x}"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 23)
        await self.server.process_message(self.client, {"id": 4, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:x}"]})
        self.assertEqual(self.writer.messages[-1]["error"][0], 23)
        counters = self.server.get_status_payload()["share_counters"]
        self.assertEqual(counters["rejected"], 2)
        self.assertEqual(counters["rejected_by_reason"]["low_difficulty"], 2)
        self.assertEqual(counters["rejected_by_reason"]["duplicate"], 0)
        self.assertEqual(counters["duplicate_retries"], 0)

    def test_status_payload_never_queries_accounting_database_inline(self):
        class ExplodingAccounting:
            def get_dashboard_stats(self):
                raise AssertionError("status request touched SQLite")

        self.server.pplns_engine = ExplodingAccounting()
        self.server._measured_status_cache = self.server._empty_measured_status()
        payload = self.server.get_status_payload()
        self.assertEqual(payload["accepted_blocks"], 0)
        self.assertIn("share_counters", payload)

    async def test_client_cannot_lower_difficulty(self):
        original = self.server.share_target
        await self.server.process_message(self.client, {"id": 5, "method": "mining.suggest_difficulty", "params": [0.000001]})
        self.assertFalse(self.writer.messages[-1]["result"])
        self.assertEqual(self.server.share_target, original)

    async def test_asic_duplicate_share_and_cross_client_isolation(self):
        job = await self._job()
        self.client.is_kaspa = True
        self.client.share_target = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
        job.share_target = self.client.share_target
        nonce = 0x0003021990B7CDB8

        # First submit from ASIC client should succeed
        await self.server.process_message(self.client, {"id": 10, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:016x}"]})
        self.assertTrue(self._response(10)["result"])
        self.assertIsNone(self._response(10)["error"])

        # The exact accepted request is idempotent: acknowledge a firmware retry
        # as success without crediting or submitting the share twice.
        await self.server.process_message(self.client, {"id": 11, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:016x}"]})
        self.assertTrue(self._response(11)["result"])
        self.assertIsNone(self._response(11)["error"])
        self.assertEqual(self.server._share_counters["duplicate_retries"], 1)
        self.assertEqual(self.client.shares_accepted, 1)

        # Different nonce on same job from same ASIC must succeed (never falsely flagged as duplicate)
        # A block submission may trigger VarDiff, so restore the deliberately permissive
        # target used by this duplicate-detection test.
        self.client.share_target = (1 << 256) - 1
        different_nonce = 0x00030385011CA4E0
        await self.server.process_message(self.client, {"id": 12, "method": "mining.submit", "params": ["worker", job.job_id, f"{different_nonce:016x}"]})
        self.assertTrue(self._response(12)["result"])
        self.assertIsNone(self._response(12)["error"])

        # The same nonce from another ASIC belongs to a different job/worker and
        # must not collide with the first client's bounded duplicate cache.
        second_writer = MockWriter()
        second = StratumClient(None, second_writer, "client-2")
        second.authorized = True
        second.is_kaspa = True
        second.worker_name = "worker-2"
        second.wallet_address = StealthKeyPair().get_public_stealth_address()
        second.share_target = (1 << 256) - 1
        self.server.clients[second.client_id] = second
        second_job = await self.server.create_new_block_job(second)
        await self.server.process_message(
            second,
            {"id": 13, "method": "mining.submit", "params": ["worker-2", second_job.job_id, f"{nonce:016x}"]},
        )
        second_response = next(message for message in second_writer.messages if message.get("id") == 13)
        self.assertTrue(second_response["result"])
        self.assertIsNone(second_response["error"])

    async def test_same_nonce_on_different_jobs_is_not_a_duplicate(self):
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        first = await self._job()
        second = await self._job()
        nonce = 0x0003000000000042

        await self.server.process_message(
            self.client,
            {"id": 20, "method": "mining.submit", "params": ["worker", first.job_id, f"{nonce:016x}"]},
        )
        self.client.share_target = (1 << 256) - 1
        await self.server.process_message(
            self.client,
            {"id": 21, "method": "mining.submit", "params": ["worker", second.job_id, f"{nonce:016x}"]},
        )

        self.assertTrue(self._response(20)["result"])
        self.assertTrue(self._response(21)["result"])

    async def test_asic_reconnect_can_finish_owned_job_without_double_credit(self):
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        job = await self._job()
        nonce = 0x0003000000000043

        reconnect_writer = MockWriter()
        reconnect = StratumClient(None, reconnect_writer, "client-reconnected")
        reconnect.authorized = True
        reconnect.is_kaspa = True
        reconnect.wallet_address = self.client.wallet_address
        reconnect.worker_name = self.client.worker_name
        reconnect.share_target = (1 << 256) - 1
        self.server.clients[reconnect.client_id] = reconnect

        submit = {
            "id": 22,
            "method": "mining.submit",
            "params": ["worker", job.job_id, f"{nonce:016x}"],
        }
        await self.server.process_message(reconnect, submit)
        self.assertTrue(reconnect_writer.messages[-1]["result"])
        self.assertEqual(self.server._share_counters["accepted"], 1)

        retry_writer = MockWriter()
        retry = StratumClient(None, retry_writer, "client-reconnected-again")
        retry.authorized = True
        retry.is_kaspa = True
        retry.wallet_address = self.client.wallet_address
        retry.worker_name = self.client.worker_name
        await self.server.process_message(retry, {**submit, "id": 23})
        self.assertTrue(retry_writer.messages[-1]["result"])
        self.assertEqual(self.server._share_counters["accepted"], 1)
        self.assertEqual(self.server._share_counters["duplicate_retries"], 1)

    async def test_reconnect_cannot_submit_another_identity_job(self):
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        job = await self._job()

        impostor_writer = MockWriter()
        impostor = StratumClient(None, impostor_writer, "client-impostor")
        impostor.authorized = True
        impostor.is_kaspa = True
        impostor.wallet_address = StealthKeyPair().get_public_stealth_address()
        impostor.worker_name = self.client.worker_name
        await self.server.process_message(
            impostor,
            {"id": 24, "method": "mining.submit", "params": ["worker", job.job_id, "1"]},
        )
        self.assertEqual(impostor_writer.messages[-1]["error"][0], 21)

    async def test_job_pow_is_identical_to_node_block_pow(self):
        job = await self._job()
        nonce = 0x0003000012345678
        self.assertEqual(job.calculate_pow(nonce).hex(), job.build_block(nonce).hash)

    async def test_pow_limit_forces_common_asic_bootstrap_difficulty(self):
        self.client.is_kaspa = True
        self.client.share_diff = 8192.0
        self.client.min_diff = 8192.0
        self.client.max_diff = 262144.0
        await self.server._issue_job(self.client)
        self.assertEqual(self.client.share_diff, 128.0)
        self.assertEqual(self.client.share_target, diff_to_kaspa_target(128.0))

    async def test_vardiff_never_becomes_harder_than_l1_target(self):
        self.client.is_kaspa = True
        self.client.share_diff = 8192.0
        bits = target_to_compact(diff_to_kaspa_target(512.0))

        await self.server._enforce_network_safe_difficulty(self.client, bits)

        self.assertGreaterEqual(self.client.share_diff, 500.0)
        self.assertLessEqual(
            self.client.share_diff,
            self.server.active_network_share_diff,
        )
        self.assertGreaterEqual(
            self.client.share_target,
            compact_to_target(bits),
        )

    async def test_vardiff_change_does_not_reject_work_from_previous_job(self):
        self.server.asic_min_share_diff = 0.000001
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        self.client.share_diff = 64.0
        job = await self._job()
        job.share_target = (1 << 256) - 1
        self.client.share_target = 1
        self.client.share_diff = 512.0
        await self.server.process_message(
            self.client,
            {"id": 30, "method": "mining.submit", "params": ["worker", job.job_id, "1"]},
        )
        self.assertTrue(self._response(30)["result"])

    async def test_vardiff_preserves_candidate_rate_with_two_miners(self):
        self.client.is_kaspa = True
        self.client.share_diff = 128.0
        self.client.min_diff = 128.0
        self.client.max_diff = 65536.0
        self.client.vardiff_window_start = time.monotonic() - 16.0
        self.client.vardiff_shares = 11  # about 41 shares/minute
        second = StratumClient(None, MockWriter(), "client-2")
        second.authorized = True
        second.is_kaspa = True
        self.server.clients[second.client_id] = second

        await self.server._check_client_vardiff(self.client)

        self.assertEqual(self.client.share_diff, 128.0)

    async def test_vardiff_raises_when_pool_has_enough_miners(self):
        self.client.is_kaspa = True
        self.client.share_diff = 128.0
        self.client.min_diff = 128.0
        self.client.max_diff = 65536.0
        self.client.vardiff_window_start = time.monotonic() - 6.0
        self.client.vardiff_shares = 13
        for index in range(2, 9):
            peer = StratumClient(None, MockWriter(), f"client-{index}")
            peer.authorized = True
            peer.is_kaspa = True
            self.server.clients[peer.client_id] = peer

        await self.server._check_client_vardiff(self.client)

        self.assertEqual(self.client.share_diff, 512.0)

    async def test_recent_late_block_candidate_is_credited_without_dag_submission(self):
        self.server.asic_min_share_diff = 0.000001
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        self.client.share_diff = 64.0
        job = await self._job()
        job.share_target = (1 << 256) - 1
        self.server.active_template_key = ("advanced-template",)
        network_target = compact_to_target(job.bits)
        nonce = next(
            candidate
            for candidate in range(256)
            if hash_to_target_int(job.calculate_pow(candidate)) <= network_target
        )
        before = len(self.daemon.dag.blocks)
        await self.server.process_message(
            self.client,
            {"id": 31, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:x}"]},
        )
        self.assertTrue(self._response(31)["result"])
        self.assertEqual(len(self.daemon.dag.blocks), before)
        self.assertEqual(self.client.blocks_found, 0)

    async def test_congested_template_only_submits_one_candidate(self):
        self.server.asic_min_share_diff = 0.000001
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        self.client.share_diff = 64.0
        self.server.network = replace(self.server.network, max_parents=1)
        first = await self._job()
        second = await self._job()
        first.share_target = (1 << 256) - 1
        second.share_target = (1 << 256) - 1
        self.assertEqual(first.template_key, second.template_key)

        target = compact_to_target(first.bits)
        first_nonce = next(
            nonce for nonce in range(256)
            if hash_to_target_int(first.calculate_pow(nonce)) <= target
        )
        second_nonce = next(
            nonce for nonce in range(256)
            if hash_to_target_int(second.calculate_pow(nonce)) <= target
        )
        before = len(self.daemon.dag.blocks)
        await self.server.process_message(
            self.client,
            {"id": 33, "method": "mining.submit", "params": ["worker", first.job_id, f"{first_nonce:x}"]},
        )
        await self.server.process_message(
            self.client,
            {"id": 34, "method": "mining.submit", "params": ["worker", second.job_id, f"{second_nonce:x}"]},
        )
        await self._drain_block_submissions()

        self.assertTrue(self._response(33)["result"])
        self.assertTrue(self._response(34)["result"])
        self.assertEqual(len(self.daemon.dag.blocks), before + 1)
        self.assertEqual(self.client.shares_accepted, 2)
        self.assertEqual(self.client.blocks_found, 1)

    async def test_clear_template_submits_bounded_sibling_batch(self):
        self.server.asic_min_share_diff = 0.000001
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        self.client.share_diff = 64.0
        first = await self._job()
        second = await self._job()
        first.share_target = (1 << 256) - 1
        second.share_target = (1 << 256) - 1
        target = compact_to_target(first.bits)
        first_nonce = next(
            nonce for nonce in range(256)
            if hash_to_target_int(first.calculate_pow(nonce)) <= target
        )
        second_nonce = next(
            nonce for nonce in range(256)
            if hash_to_target_int(second.calculate_pow(nonce)) <= target
        )
        before = len(self.daemon.dag.blocks)
        await self.server.process_message(
            self.client,
            {"id": 35, "method": "mining.submit", "params": ["worker", first.job_id, f"{first_nonce:x}"]},
        )
        await self.server.process_message(
            self.client,
            {"id": 36, "method": "mining.submit", "params": ["worker", second.job_id, f"{second_nonce:x}"]},
        )
        await self._drain_block_submissions()
        self.assertTrue(self._response(35)["result"])
        self.assertTrue(self._response(36)["result"])
        self.assertEqual(len(self.daemon.dag.blocks), before + 2)
        self.assertEqual(self.client.blocks_found, 2)

    async def test_old_block_candidate_outside_grace_is_only_credited(self):
        self.server.asic_min_share_diff = 0.000001
        self.client.is_kaspa = True
        self.client.share_target = (1 << 256) - 1
        self.client.share_diff = 64.0
        job = await self._job()
        job.share_target = (1 << 256) - 1
        job.created_at -= self.server.late_block_grace_seconds + 1.0
        self.server.active_template_key = ("advanced-template",)
        network_target = compact_to_target(job.bits)
        nonce = next(
            candidate
            for candidate in range(256)
            if hash_to_target_int(job.calculate_pow(candidate)) <= network_target
        )
        before = len(self.daemon.dag.blocks)
        await self.server.process_message(
            self.client,
            {"id": 32, "method": "mining.submit", "params": ["worker", job.job_id, f"{nonce:x}"]},
        )
        self.assertTrue(self._response(32)["result"])
        self.assertEqual(len(self.daemon.dag.blocks), before)

    async def test_pool_fee_uses_time_decay_schedule(self):
        launch = 1_700_000_000.0
        self.server.pool_launch_time = launch
        with patch("stratum.stratum_server.time.time", return_value=launch + 6 * 86400):
            self.assertEqual(self.server.get_pool_fee_rate(), 0.05)
        with patch("stratum.stratum_server.time.time", return_value=launch + 7 * 86400):
            self.assertEqual(self.server.get_pool_fee_rate(), 0.03)
        with patch("stratum.stratum_server.time.time", return_value=launch + 30 * 86400):
            self.assertEqual(self.server.get_pool_fee_rate(), 0.01)

    async def test_template_refresh_uses_fixed_deadlines(self):
        self.server.template_refresh_seconds = 0.04
        calls = []
        clock = [1000.0]

        async def advance_clock(seconds):
            clock[0] += seconds

        async def slow_broadcast(*, force=False):
            calls.append(clock[0])
            clock[0] += 0.025
            if len(calls) == 4:
                self.server.running = False
            return True

        self.server._broadcast_template = slow_broadcast
        self.server.running = True
        try:
            # Verify deadline arithmetic, not whether a loaded host schedules
            # four real sleeps in190ms. A relative-sleep implementation would
            # accumulate the25ms broadcast cost and fail the interval check.
            with patch('stratum.stratum_server.time',
                       SimpleNamespace(monotonic=lambda: clock[0])), \
                 patch('stratum.stratum_server.asyncio.sleep', side_effect=advance_clock):
                await self.server._refresh_loop()
        finally:
            self.server.running = False

        self.assertEqual(len(calls), 4)
        intervals = [right - left for left, right in zip(calls, calls[1:])]
        for interval in intervals:
            self.assertAlmostEqual(interval, 0.04, places=7)

    async def test_template_broadcast_isolates_one_failed_client(self):
        second = StratumClient(None, MockWriter(), "client-2")
        second.authorized = True
        second.is_kaspa = True
        second.worker_name = "second"
        second.wallet_address = StealthKeyPair().get_public_stealth_address()
        self.server.clients[second.client_id] = second
        delivered = []
        original = self.server._issue_job

        async def isolated_issue(client, **kwargs):
            if client is self.client:
                raise ConnectionError("closed test socket")
            delivered.append(client.client_id)
            return await original(client, **kwargs)

        self.server._issue_job = isolated_issue
        self.assertTrue(await self.server._broadcast_template(force=True))
        self.assertEqual(delivered, [second.client_id])

    async def test_template_refresh_retries_unchanged_parent_without_full_interval(self):
        self.server.template_refresh_seconds = 0.08
        self.server.template_retry_seconds = 0.01
        calls = []

        async def unchanged_then_new(*, force=False):
            calls.append(time.monotonic())
            return len(calls) >= 2

        self.server._broadcast_template = unchanged_then_new
        self.server.running = True
        task = asyncio.create_task(self.server._refresh_loop())
        try:
            await asyncio.sleep(0.13)
        finally:
            self.server.running = False
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertGreaterEqual(len(calls), 2)
        self.assertLess(calls[1] - calls[0], self.server.template_refresh_seconds / 2)

    async def test_template_refresh_holds_underfilled_asic_batch_until_quota(self):
        self.server.template_refresh_seconds = 0.04
        self.server.template_retry_seconds = 0.01
        self.server.active_template_key = ("active",)
        self.server.active_template_started_at = time.monotonic()
        self.server.active_template_submission_limit = 8
        self.server.template_submission_counts[("active",)] = 3
        calls = []

        async def broadcast(*, force=False):
            calls.append(time.monotonic())
            return True

        self.server._broadcast_template = broadcast
        self.server.running = True
        task = asyncio.create_task(self.server._refresh_loop())
        try:
            await asyncio.sleep(0.055)
            self.assertEqual(calls, [])
            self.server.template_submission_counts[("active",)] = 8
            await asyncio.sleep(0.025)
        finally:
            self.server.running = False
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertGreaterEqual(len(calls), 1)

    async def test_one_block_merge_batch_rotates_as_soon_as_quota_is_filled(self):
        self.server.template_refresh_seconds = 0.04
        self.server.template_retry_seconds = 0.01
        calls = []

        async def broadcast(*, force=False):
            calls.append(time.monotonic())
            if len(calls) == 1:
                self.server.active_template_key = ("merge",)
                self.server.active_template_started_at = time.monotonic()
                self.server.active_template_submission_limit = 1
            return True

        self.server._broadcast_template = broadcast
        self.server.running = True
        task = asyncio.create_task(self.server._refresh_loop())
        try:
            await asyncio.sleep(0.052)
            self.server.template_submission_counts[("merge",)] = 1
            await asyncio.sleep(0.025)
        finally:
            self.server.running = False
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertGreaterEqual(len(calls), 2)
        self.assertLess(calls[1] - calls[0], self.server.template_refresh_seconds)


if __name__ == "__main__":
    unittest.main()
