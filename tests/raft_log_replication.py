import sys
import asyncio
import logging

import os
import sys
import time

import pycyphal
import sirius_cyber_corp
import uavcan

# Add parent directory to Python path
sys.path.append(os.path.abspath("/Users/maksimdrachov/cyraft"))  # Q: how to make it relative?``
from cyraft import RaftNode
from cyraft import RaftState

_logger = logging.getLogger(__name__)

"""
Test the Raft log replication.

TEST 1: Send a single valid request to the leader node and check if it is replicated to the follower nodes.

TEST 2: Send a single invalid request to the leader node and check if it is not replicated to the follower nodes.

TEST 3: Send multiple valid requests to the leader node and check if they are replicated to the follower nodes.

TEST 4: Send a single valid request, replacing an existing entry, to the leader node and
check if it is replicated to the follower nodes.
"""


async def _unittest_raft_log() -> None:
    logging.root.setLevel(logging.INFO)

    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__CLN__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"
    os.environ["UAVCAN__CLN__APPEND_ENTRIES__ID"] = "2"

    TERM_TIMEOUT = 0.5
    ELECTION_TIMEOUT = 5

    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node_1 = RaftNode()
    raft_node_1.term_timeout = TERM_TIMEOUT
    raft_node_1.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "42"
    raft_node_2 = RaftNode()
    raft_node_2.term_timeout = TERM_TIMEOUT
    raft_node_2.election_timeout = ELECTION_TIMEOUT + 1
    os.environ["UAVCAN__NODE__ID"] = "43"
    raft_node_3 = RaftNode()
    raft_node_3.term_timeout = TERM_TIMEOUT
    raft_node_3.election_timeout = ELECTION_TIMEOUT + 1

    # make all part of the same cluster
    cluster = [raft_node_1._node.id, raft_node_2._node.id, raft_node_3._node.id]
    raft_node_1.add_remote_node(cluster)
    raft_node_2.add_remote_node(cluster)
    raft_node_3.add_remote_node(cluster)

    # start all nodes
    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())

    # wait for the leader to be elected
    await asyncio.sleep(ELECTION_TIMEOUT + 1)

    # check if the leader is elected
    assert raft_node_1._state == RaftState.LEADER
    assert raft_node_2._state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_1._voted_for == 41
    assert raft_node_2._voted_for == 41
    assert raft_node_3._voted_for == 41

    # send a request to the leader
    new_entry = sirius_cyber_corp.LogEntry_1(
        term=1,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_1"),
            value=1,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=raft_node_1._term,
        prev_log_index=0,
        prev_log_term=0,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=1,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node_1._serve_append_entries(request, metadata)
    assert response.success == True

    # wait for the request to be replicated
    await asyncio.sleep(TERM_TIMEOUT + 1)

    # check if the new entry is replicated in the leader node
    assert raft_node_1._log[1].term == request.term
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 1

    # check if the new entry is replicated in the follower nodes
    assert raft_node_1._log[2].term == request.term
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[2].entry.value == 1
    assert raft_node_1._log[3].term == request.term
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[3].entry.value == 1
