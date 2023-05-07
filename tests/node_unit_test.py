import sys
import asyncio
import logging

import os
import sys
import time

# Add parent directory to Python path
sys.path.append(os.path.abspath("/Users/maksimdrachov/cyraft"))  # Q: how to make it relative?``
from cyraft import RaftNode
from cyraft import RaftState

_logger = logging.getLogger(__name__)


async def _unittest_raft_node_init() -> None:
    """
    Test that node can be initialized

    Test add_remote_node and remove_remote_node methods
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"

    raft_node = RaftNode()
    assert raft_node._node.id == 41
    assert raft_node.prev_state == RaftState.FOLLOWER
    assert raft_node.state == RaftState.FOLLOWER

    assert len(raft_node.cluster) == 0
    assert len(raft_node.request_vote_clients) == 0
    assert len(raft_node.append_entries_clients) == 0

    assert raft_node.current_term == 0
    assert raft_node.voted_for is None
    assert len(raft_node.log) == 1  # Log is initialized with one (empty) entry
    assert raft_node.log[0].term == 0
    assert raft_node.log[0].entry.value == 0  # Empty entry

    assert raft_node.commit_index == 0

    assert raft_node.next_index == []  # no remote nodes
    assert raft_node.match_index == []

    ### Test add_remote_node and remove_remote_node methods ###
    raft_node.add_remote_node(42)
    assert len(raft_node.cluster) == 1
    assert raft_node.cluster[0] == 42
    assert len(raft_node.request_vote_clients) == 1
    assert len(raft_node.append_entries_clients) == 1
    assert raft_node.next_index == [1]
    assert raft_node.match_index == [0]

    raft_node.remove_remote_node(42)
    assert len(raft_node.cluster) == 0
    assert len(raft_node.request_vote_clients) == 0
    assert len(raft_node.append_entries_clients) == 0
    assert raft_node.next_index == []
    assert raft_node.match_index == []

    raft_node.add_remote_node([42, 43])
    assert len(raft_node.cluster) == 2
    assert raft_node.cluster[0] == 42
    assert raft_node.cluster[1] == 43
    assert len(raft_node.request_vote_clients) == 2
    assert len(raft_node.append_entries_clients) == 2
    assert raft_node.next_index == [1, 1]
    assert raft_node.match_index == [0, 0]

    raft_node.remove_remote_node(42)
    assert len(raft_node.cluster) == 1
    assert raft_node.cluster[0] == 43
    assert len(raft_node.request_vote_clients) == 1
    assert len(raft_node.append_entries_clients) == 1
    assert raft_node.next_index == [1]
    assert raft_node.match_index == [0]


async def _unittest_raft_node_term_timeout() -> None:
    # TODO: come back to this, once the timer functionality is implemented as it should be
    pass


async def _unittest_raft_node_election_timeout() -> None:
    # TODO: come back to this, once the timer functionality is implemented as it should be
    pass
