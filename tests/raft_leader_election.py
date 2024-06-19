# - [ ] at any given time each server is in one of three states: leader, follower or candidate
# - [ ] raft divides time into terms of arbitrary length, terms are numbered in concecutive integers
# - [ ] current terms exchanged whenever servers communicate; if one server's current term is smaller than the other's, then it updates its current time term
# - [ ] if a candidate or leader discovers that its term is out of date, it immediately reverts to follower state
# - [ ] if a server receives a request with a stale term number, it rejects the request
# - [ ] RequestVote RPCs are initiated by candidates during elections
# - [ ] when server start up, they begin as followers
# - [ ] server remains in follower state as long as it receives valid RPCs from a leader or candidate
# - [ ] leaders send periodic heartbeats (AppendEntries RPC that carry no log entries) to all followers to maintain authority
# - [ ] if a follower receives no communication over a period of time called the election timeout, then it begins an election to choose a new leader
# - [ ] To begin an election, a follower increments its current term and transitions to candidate state
# - [ ] it then votes for itself and issues RequestVote RPCs in parallel to each of the other servers in the cluster
# - [ ] once a candidate wins an election, it becomes a leader
# - [ ] it then sends heartbeat messages to all of the other servers to establish authority and prevent new elections
# - [ ] raft uses randomized election timeouts to ensure that split vote are rare and are resolved quickly


import sys
import asyncio
import logging

import os
import sys
import time

# Add parent directory to Python path
# Get the absolute path of the parent directory
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Add the parent directory to the Python path
sys.path.append(parent_dir)
# This can be removed if setting PYTHONPATH (export PYTHONPATH=cyraft)

from cyraft import RaftNode
from cyraft import RaftState

_logger = logging.getLogger(__name__)

"""
Test the Raft FSM (see Figure 4, https://raft.github.io/raft.pdf)
(Our implementation has a small difference: no transition from CANDIDATE to CANDIDATE, instead it goes back to FOLLOWER)

- at any given time each server is in one of three states: leader, follower or candidate
- state transitions:
    - FOLLOWER
        - starts as a follower (1)
        - times out, starts election -> CANDIDATE (2)
    - CANDIDATE
        - if election timeout elapses -> FOLLOWER (3)
        - if votes received from majority of servers -> LEADER (4)
        - discovers current leader or new term -> FOLLOWER (5.1, 5.2)
    - LEADER
        - discovers server with higher term -> FOLLOWER (6)

Node 1                      Node 2                        Node 3                       TEST STAGES
+-------------------------+ +---------------------------+ +-------------------------+
        |(1)                        |(1)                          |(1)
        V                           V                             V
        Follower                    Follower                      Follower              [1]
------------------------------------Election timeout---------------------------------
        |(2)                        |                             |
        V                           V                             V
        Candidate                   Follower                      Follower              [2]
        |(4)                        |                             |
        V                           V                             V
        Leader                      Follower                      Follower              [3]
------------------------------------Election timeouts--------------------------------
        Leader                      Follower                      Follower              [4]
====================================Hard (re)set=====================================
        Leader                      Leader (higher term than 1)   Follower              [5]
        |(6)                        |                             |
        V                           V                             V
        Follower                    Leader                        Follower              [6]
====================================Hard (re)set=====================================
        Candidate                   Leader                        Follower              [7]
        |(5.1)                      |                             |
        V                           V                             V
        Follower                    Leader                        Follower              [8]
====================================Hard (re)set=====================================
        Candidate                   Candidate (higher term)       Follower              [9]
        |(5.2)                      |                             |
        V                           V                             V
        Follower                    Leader                        Follower              [10]

To check on every state transition:
- prev_state
- state
- current_term
- voted_for
"""


async def _unittest_raft_fsm_1() -> None:
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
    raft_node_2.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "43"
    raft_node_3 = RaftNode()
    raft_node_3.term_timeout = TERM_TIMEOUT
    raft_node_3.election_timeout = ELECTION_TIMEOUT

    # make all part of the same cluster
    cluster = [raft_node_1._node.id, raft_node_2._node.id, raft_node_3._node.id]
    raft_node_1.add_remote_node(cluster)
    raft_node_2.add_remote_node(cluster)
    raft_node_3.add_remote_node(cluster)

    _logger.info("================== TEST STAGE 1: INIT nodes ==================")

    assert raft_node_1._prev_state == RaftState.FOLLOWER
    assert raft_node_1._state == RaftState.FOLLOWER
    assert raft_node_1._term == 0
    assert raft_node_1._voted_for is None

    assert raft_node_2._prev_state == RaftState.FOLLOWER
    assert raft_node_2._state == RaftState.FOLLOWER
    assert raft_node_2._term == 0
    assert raft_node_2._voted_for is None

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._term == 0
    assert raft_node_3._voted_for is None

    _logger.info("================== TEST STAGE 2/3: LEADER election ==================")

    # node 1 should have a smaller election timeout than others
    raft_node_1.election_timeout = ELECTION_TIMEOUT - 1

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())
    # let run until election timeout
    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._state == RaftState.LEADER
    assert (
        raft_node_1._term == 2
    ), "+1 due to starting election, +1 due to term timeout (this last one is not guaranteed?)"
    assert raft_node_1._voted_for == 41

    assert raft_node_2._prev_state == RaftState.FOLLOWER
    assert raft_node_2._state == RaftState.FOLLOWER
    assert raft_node_2._term == 2, "received heartbeat from LEADER"
    assert raft_node_2._voted_for == 41

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._term == 2, "received heartbeat from LEADER"
    assert raft_node_3._voted_for == 41

    assert raft_node_1._term >= raft_node_2._term, "LEADER term should be higher (or equal) than FOLLOWER"
    assert raft_node_1._term >= raft_node_3._term, "LEADER term should be higher (or equal) than FOLLOWER"

    _logger.info("================== TEST STAGE 4: LEADER maintains leadership ==================")

    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._state == RaftState.LEADER
    assert raft_node_1._term == 12, "+ 10 due to term timeout"
    assert raft_node_1._voted_for == 41

    assert raft_node_2._prev_state == RaftState.FOLLOWER
    assert raft_node_2._state == RaftState.FOLLOWER
    assert raft_node_1._term == 12, "received heartbeat from LEADER"
    assert raft_node_2._voted_for == 41

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_1._term == 12, "received heartbeat from LEADER"
    assert raft_node_3._voted_for == 41

    assert raft_node_1._term >= raft_node_2._term
    assert raft_node_1._term >= raft_node_3._term

    raft_node_1.close()
    raft_node_2.close()
    raft_node_3.close()
    _logger.info("================== DOES SOMETHING HAPPEN AFTER THIS? ==================")
    await asyncio.sleep(10)


async def _unittest_raft_fsm_2():
    logging.root.setLevel(logging.INFO)

    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__CLN__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"
    os.environ["UAVCAN__CLN__APPEND_ENTRIES__ID"] = "2"

    TERM_TIMEOUT = 0.5
    ELECTION_TIMEOUT = 5

    _logger.info("================== TEST STAGE 5/6: node with higher term gets elected ==================")

    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node_1 = RaftNode()
    raft_node_1.term_timeout = TERM_TIMEOUT
    raft_node_1.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "42"
    raft_node_2 = RaftNode()
    raft_node_2.term_timeout = TERM_TIMEOUT
    raft_node_2.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "43"
    raft_node_3 = RaftNode()
    raft_node_3.term_timeout = TERM_TIMEOUT
    raft_node_3.election_timeout = ELECTION_TIMEOUT + 1  # we don't want this node starting any elections

    # make all part of the same cluster
    cluster = [raft_node_1._node.id, raft_node_2._node.id, raft_node_3._node.id]
    raft_node_1.add_remote_node(cluster)
    raft_node_2.add_remote_node(cluster)
    raft_node_3.add_remote_node(cluster)

    # make both node 1 and 2 LEADER, but node 2 has the higher ground (term)
    raft_node_1._change_state(RaftState.CANDIDATE)
    raft_node_1._change_state(RaftState.LEADER)
    raft_node_1._voted_for = 41
    raft_node_1._term = 1
    raft_node_2._change_state(RaftState.CANDIDATE)  # this is not strictly necessary, but it's more realistic
    raft_node_2._change_state(RaftState.LEADER)
    raft_node_2._voted_for = 42
    raft_node_2._term = 2

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())

    await asyncio.sleep(ELECTION_TIMEOUT + 0.1)  # + 0.1 is necesary to make sure the last term timeout is processed

    # assert raft_node_1._prev_state == RaftState.LEADER
    assert raft_node_1._state == RaftState.FOLLOWER
    assert raft_node_1._term == 12, "received heartbeat from LEADER"
    assert raft_node_1._voted_for == 42

    # assert raft_node_2._prev_state == RaftState.CANDIDATE
    assert raft_node_2._state == RaftState.LEADER
    assert raft_node_2._term == 12, "+ 10 due to term timeout"
    assert raft_node_2._voted_for == 42

    # assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._term == 12, "received heartbeat from LEADER"
    assert raft_node_3._voted_for == 42

    assert raft_node_2._term >= raft_node_1._term
    assert raft_node_2._term >= raft_node_3._term

    raft_node_1.close()
    raft_node_2.close()
    raft_node_3.close()
    await asyncio.sleep(1)


async def _unittest_raft_fsm_3():
    logging.root.setLevel(logging.INFO)

    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__CLN__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"
    os.environ["UAVCAN__CLN__APPEND_ENTRIES__ID"] = "2"

    TERM_TIMEOUT = 0.5
    ELECTION_TIMEOUT = 5

    _logger.info(
        "================== TEST STAGE 7/8: 1 CANDIDATE, 1 LEADER, leader asserts dominance =================="
    )

    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node_1 = RaftNode()
    raft_node_1.term_timeout = TERM_TIMEOUT
    raft_node_1.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "42"
    raft_node_2 = RaftNode()
    raft_node_2.term_timeout = TERM_TIMEOUT
    raft_node_2.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "43"
    raft_node_3 = RaftNode()
    raft_node_3.term_timeout = TERM_TIMEOUT
    raft_node_3.election_timeout = ELECTION_TIMEOUT

    # make all part of the same cluster
    cluster = [raft_node_1._node.id, raft_node_2._node.id, raft_node_3._node.id]
    raft_node_1.add_remote_node(cluster)
    raft_node_2.add_remote_node(cluster)
    raft_node_3.add_remote_node(cluster)

    # node 1 is CANDIDATE, node 2 is LEADER
    raft_node_1._change_state(RaftState.CANDIDATE)
    raft_node_1._voted_for = 41
    raft_node_2._change_state(RaftState.CANDIDATE)  # this is not strictly necessary, but it's more realistic
    raft_node_2._change_state(RaftState.LEADER)
    raft_node_2._voted_for = 42
    raft_node_2._term = 1
    raft_node_3._change_state(RaftState.FOLLOWER)
    raft_node_3._voted_for = 42

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())

    await asyncio.sleep(ELECTION_TIMEOUT + 0.1)

    # assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._state == RaftState.FOLLOWER
    assert raft_node_1._term == 11, "received heartbeat from LEADER"
    assert raft_node_1._voted_for == 42

    # assert raft_node_2._prev_state == RaftState.CANDIDATE
    assert raft_node_2._state == RaftState.LEADER
    assert raft_node_2._term == 11, "+ 10 due to term timeout"
    assert raft_node_2._voted_for == 42

    # assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._term == 11, "received heartbeat from LEADER"
    assert raft_node_3._voted_for == 42

    assert raft_node_2._term >= raft_node_1._term
    assert raft_node_2._term >= raft_node_3._term

    raft_node_1.close()
    raft_node_2.close()
    raft_node_3.close()
    await asyncio.sleep(1)


async def _unittest_raft_fsm_4():
    logging.root.setLevel(logging.INFO)

    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__CLN__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"
    os.environ["UAVCAN__CLN__APPEND_ENTRIES__ID"] = "2"

    TERM_TIMEOUT = 0.5
    ELECTION_TIMEOUT = 5

    _logger.info("================== TEST STAGE 9/10: 2 CANDIDATEs, higher term get's elected ==================")

    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node_1 = RaftNode()
    raft_node_1.term_timeout = TERM_TIMEOUT
    raft_node_1.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "42"
    raft_node_2 = RaftNode()
    raft_node_2.term_timeout = TERM_TIMEOUT
    raft_node_2.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "43"
    raft_node_3 = RaftNode()
    raft_node_3.term_timeout = TERM_TIMEOUT
    raft_node_3.election_timeout = (
        ELECTION_TIMEOUT + 1
    )  # TODO: Figure out why this election gets triggered? It should be reset?

    # make all part of the same cluster
    cluster = [raft_node_1._node.id, raft_node_2._node.id, raft_node_3._node.id]
    raft_node_1.add_remote_node(cluster)
    raft_node_2.add_remote_node(cluster)
    raft_node_3.add_remote_node(cluster)

    # node 1 is CANDIDATE, node 2 is LEADER
    raft_node_1._change_state(RaftState.CANDIDATE)
    raft_node_1._voted_for = 41
    raft_node_2._change_state(RaftState.CANDIDATE)
    raft_node_2._voted_for = 42
    raft_node_2._term = 1
    raft_node_3._change_state(RaftState.FOLLOWER)
    raft_node_3._voted_for = 42

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())

    await raft_node_1._start_election()
    await raft_node_2._start_election()

    await asyncio.sleep(ELECTION_TIMEOUT + 0.1)

    assert raft_node_1._prev_state == RaftState.FOLLOWER
    assert raft_node_1._state == RaftState.FOLLOWER
    assert raft_node_1._term == 12, "received heartbeat from LEADER"
    assert raft_node_1._voted_for == 42

    assert raft_node_2._prev_state == RaftState.CANDIDATE
    assert raft_node_2._state == RaftState.LEADER
    assert raft_node_2._term == 12, "+ 1 due to election timeout, + 10 due to term timeout"
    assert raft_node_2._voted_for == 42

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._term == 12, "received heartbeat from LEADER"
    assert raft_node_3._voted_for == 42

    assert raft_node_2._term >= raft_node_1._term
    assert raft_node_2._term >= raft_node_3._term

    raft_node_1.close()
    raft_node_2.close()
    raft_node_3.close()
    await asyncio.sleep(1)
