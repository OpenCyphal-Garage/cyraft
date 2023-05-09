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
    Test that node is initialized correctly

    Test add_remote_node and remove_remote_node methods
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"

    raft_node = RaftNode()
    assert raft_node._node.id == 41
    assert raft_node._prev_state == RaftState.FOLLOWER
    assert raft_node._state == RaftState.FOLLOWER

    assert len(raft_node._cluster) == 0
    assert len(raft_node._request_vote_clients) == 0
    assert len(raft_node._append_entries_clients) == 0

    assert raft_node._term == 0
    assert raft_node._voted_for is None
    assert len(raft_node._log) == 1  # Log is initialized with one (empty) entry
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.value == 0  # Empty entry

    assert raft_node._commit_index == 0

    assert raft_node._next_index == []  # no remote nodes
    assert raft_node._match_index == []

    # Adding a single node (same node id, so should not be added)
    raft_node.add_remote_node(41)
    assert len(raft_node._cluster) == 0
    assert len(raft_node._request_vote_clients) == 0
    assert len(raft_node._append_entries_clients) == 0
    assert raft_node._next_index == []
    assert raft_node._match_index == []

    # Adding a single node (different node id, so should be added)
    raft_node.add_remote_node(42)
    assert len(raft_node._cluster) == 1
    assert raft_node._cluster[0] == 42
    assert len(raft_node._request_vote_clients) == 1
    assert len(raft_node._append_entries_clients) == 1
    assert raft_node._next_index == [1]
    assert raft_node._match_index == [0]

    # Removing a single node (should become empty cluster)
    raft_node.remove_remote_node(42)
    assert len(raft_node._cluster) == 0
    assert len(raft_node._request_vote_clients) == 0
    assert len(raft_node._append_entries_clients) == 0
    assert raft_node._next_index == []
    assert raft_node._match_index == []

    # Adding multiple nodes
    raft_node.add_remote_node([42, 43])
    assert len(raft_node._cluster) == 2
    assert raft_node._cluster[0] == 42
    assert raft_node._cluster[1] == 43
    assert len(raft_node._request_vote_clients) == 2
    assert len(raft_node._append_entries_clients) == 2
    assert raft_node._next_index == [1, 1]
    assert raft_node._match_index == [0, 0]

    # Remove one of the nodes, make sure other one is still there
    raft_node.remove_remote_node(42)
    assert len(raft_node._cluster) == 1
    assert raft_node._cluster[0] == 43
    assert len(raft_node._request_vote_clients) == 1
    assert len(raft_node._append_entries_clients) == 1
    assert raft_node._next_index == [1]
    assert raft_node._match_index == [0]


async def _unittest_raft_node_term_timeout() -> None:
    """
    Test that the LEADER node term is increased upon term timeout

    Test that the CANDIDATE/FOLLOWER node term is not increased upon term timeout
    """

    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    ELECTION_TIMEOUT = 10  # so that we don't start an election
    TERM_TIMEOUT = 1
    raft_node.election_timeout = ELECTION_TIMEOUT
    raft_node.term_timeout = TERM_TIMEOUT

    raft_node.change_state(RaftState.LEADER)  # only leader can increase term
    asyncio.create_task(raft_node.run())
    await asyncio.sleep(TERM_TIMEOUT + 0.1)  # + 0.1 to make sure the timer has been reset
    assert raft_node._term == 1
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node._term == 2
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node._term == 3

    raft_node.change_state(RaftState.CANDIDATE)  # candidate should not increase term
    await asyncio.sleep(TERM_TIMEOUT + 0.1)
    assert raft_node._term == 3

    raft_node.change_state(RaftState.FOLLOWER)  # follower should not increase term
    await asyncio.sleep(TERM_TIMEOUT + 0.1)
    assert raft_node._term == 3

    raft_node.close()
    await asyncio.sleep(1)  # give some time for the node to close


async def _unittest_raft_node_election_timeout() -> None:
    """
    Test that the node converts to candidate after the election timeout
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    ELECTION_TIMEOUT = 5
    raft_node.election_timeout = ELECTION_TIMEOUT

    assert raft_node._state == RaftState.FOLLOWER
    asyncio.create_task(raft_node.run())

    await asyncio.sleep(ELECTION_TIMEOUT + 0.1)
    assert raft_node._prev_state == RaftState.CANDIDATE
    assert raft_node._state == RaftState.LEADER

    raft_node.close()
    await asyncio.sleep(1)  # fixes when just running this test, however not when "pytest /cyraft" is run
