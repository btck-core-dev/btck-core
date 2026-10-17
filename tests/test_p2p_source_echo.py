"""A completed ancestor segment must not flood its own source with echoes."""
import asyncio
import unittest
from node.blockdag import BlockDAG
from p2p.p2p_network import P2PNetwork
from p2p.managed_sync import ManagedSyncNetwork
from tests.pow_helpers import mine_block

class HeadOnlySource(P2PNetwork):
    async def _process_msg(self,peer,message):
        if message.get('type')=='GET_BLOCKS' and peer.handshake_complete:
            await peer.send_msg('INV',{'blocks':[self.dag.selected_tip],'more':False})
        else:await super()._process_msg(peer,message)

class SourceEchoTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_long_ancestor_completion_keeps_upstream_connected(self):
        dag=BlockDAG(network='simnet')
        cb=(bytes.fromhex('02'+'77'*32),bytes.fromhex('03'+'88'*32))
        for i in range(180):
            b=mine_block(dag,parents=dag.get_tips(),timestamp=1788912100+i,coinbase_output=cb)
            self.assertTrue(dag.add_block(b))
        source=HeadOnlySource(dag,p2p_host='127.0.0.1',p2p_port=0)
        await source.start();port=source.server.sockets[0].getsockname()[1]
        net=ManagedSyncNetwork(BlockDAG(network='simnet'),p2p_host='127.0.0.1',p2p_port=0,
            managed_peers=[f'127.0.0.1:{port}'],requests_per_second=3000,maintenance_interval=.005)
        try:
            await net.start()
            async with asyncio.timeout(15):
                while len(net.dag.blocks)!=len(dag.blocks):await asyncio.sleep(.005)
            await asyncio.sleep(.05)
            self.assertEqual(net.dag.state_root(),dag.state_root())
            self.assertGreaterEqual(net.sync_counters['orphan_accepted'],128)
            self.assertEqual(net.sync_counters['relay_capacity'],0)
            self.assertEqual(net.sync_counters['disconnected'],0)
            self.assertEqual(len(net.peers),1)
        finally:await net.stop();await source.stop()

    async def test_orphan_relays_to_other_peer_not_its_original_provider(self):
        from unittest.mock import Mock,AsyncMock
        from p2p.managed_sync import BoundedPeerConnection
        from tests.test_p2p_sync_regressions import wire
        dag=BlockDAG(network='simnet');cb=(bytes.fromhex('02'+'77'*32),bytes.fromhex('03'+'88'*32))
        blocks=[]
        for i in range(2):
            b=mine_block(dag,parents=dag.get_tips(),timestamp=1788912100+i,coinbase_output=cb)
            self.assertTrue(dag.add_block(b));blocks.append(b)
        net=ManagedSyncNetwork(BlockDAG(network='simnet'),requests_per_second=100000)
        peers=[]
        for port in [9,10]:
            writer=Mock();writer.is_closing.return_value=False
            peer=BoundedPeerConnection(None,writer,f'127.0.0.1:{port}',net.network_magic,send_timeout=1)
            peer.handshake_complete=True;peer.send_msg=AsyncMock()
            net.peers[peer.peer_addr]=peer;peers.append(peer)
        a,c=peers
        await net._process_msg(a,{'type':'BLOCK','payload':wire(blocks[1])})
        self.assertEqual(net._orphan_sources[blocks[1].hash],a.peer_addr)
        await net._process_msg(c,{'type':'BLOCK','payload':wire(blocks[0])})
        await asyncio.sleep(.02)
        def hints(peer):return [call.args[1]['blocks'][0] for call in peer.send_msg.await_args_list if call.args[0]=='INV']
        self.assertEqual(hints(a),[blocks[0].hash])
        self.assertEqual(hints(c),[blocks[1].hash])
        self.assertFalse(net._orphan_sources)
        self.assertEqual(net.dag.state_root(),dag.state_root())
