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
sys.path.append(os.path.abspath("/Users/maksimdrachov/cyraft"))  # Q: how to make it relative?``
from cyraft import RaftNode
from cyraft import RaftState

_logger = logging.getLogger(__name__)


async def _unittest_raft_fsm() -> None:
    """
    Test the Raft FSM (see Figure 4, https://raft.github.io/raft.pdf)

    - at any given time each server is in one of three states: leader, follower or candidate
    - state transitions:
        - FOLLOWER
            - starts as a follower (1)
            - times out, starts election -> CANDIDATE  (2)
        - CANDIDATE
            - if election timeout elapses: start new election -> CANDIDATE (3)
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
            Follower                    Disabled                      Disabled              [5]
            |(2)                        |                             |
            V                           V                             V
            Candidate                   Disabled                      Disabled              [6]
            |(3)                        |                             |
            V                           V                             V
            Candidate                   Disabled                      Disabled              [7]
    ====================================Hard (re)set=====================================
            Leader                      Leader (higher term than 1)   Follower              [8]
            |(6)                        |                             |
            V                           V                             V
            Follower                    Leader                        Follower              [9]
    ====================================Hard (re)set=====================================
            Candidate                   Leader                        Follower              [10]
            |(5.1)                      |                             |
            V                           V                             V
            Follower                    Leader                        Follower              [11]
    ====================================Hard (re)set=====================================
            Candidate                   Candidate (higher term)       Follower              [12]
            |(5.2)                      |                             |
            V                           V                             V
            Follower                    Leader                        Follower              [13]

    To check on every state transition:
    - prev_state
    - state
    - current_term
    - voted_for
    """

    #### SETUP ####
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
    assert raft_node_1._voted_for == 41

    assert raft_node_2._prev_state == RaftState.FOLLOWER
    assert raft_node_2._state == RaftState.FOLLOWER
    assert raft_node_2._voted_for == 41

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._voted_for == 41

    assert raft_node_1._term >= raft_node_2._term, "LEADER term should be higher (or equal) than FOLLOWER"
    assert raft_node_1._term >= raft_node_3._term, "LEADER term should be higher (or equal) than FOLLOWER"

    _logger.info("================== TEST STAGE 4: LEADER maintains leadership ==================")

    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._state == RaftState.LEADER
    assert raft_node_1._voted_for == 41

    assert raft_node_2._prev_state == RaftState.FOLLOWER
    assert raft_node_2._state == RaftState.FOLLOWER
    assert raft_node_2._voted_for == 41

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._voted_for == 41

    assert raft_node_1._term >= raft_node_2._term
    assert raft_node_1._term >= raft_node_3._term

    _logger.info("================== TEST STAGE 5/6/7: node can't become LEADER, stays CANDIDATE ==================")

    raft_node_2.close()
    raft_node_3.close()

    raft_node_1._change_state(RaftState.FOLLOWER)
    raft_node_1._voted_for = None

    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._state == RaftState.CANDIDATE
    assert raft_node_1._voted_for == 41

    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._state == RaftState.CANDIDATE
    assert raft_node_1._voted_for == 41

    _logger.info("================== TEST STAGE 8/9: node with higher term gets elected ==================")

    raft_node_1.close()  # let's just reset, so we can start from scratch; should be able to do that in life as well :'(
    del raft_node_1
    del raft_node_2
    del raft_node_3

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

    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._prev_state == RaftState.LEADER
    assert raft_node_1._state == RaftState.FOLLOWER
    assert raft_node_1._voted_for == 42

    assert raft_node_2._prev_state == RaftState.CANDIDATE
    assert raft_node_2._state == RaftState.LEADER
    assert raft_node_2._voted_for == 42

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._voted_for == 42

    assert raft_node_2._term >= raft_node_1._term
    assert raft_node_2._term >= raft_node_3._term

    _logger.info(
        "================== TEST STAGE 10/11: 1 CANDIDATE, 1 LEADER, leader asserts dominance =================="
    )

    raft_node_1.close()
    del raft_node_1
    del raft_node_2
    del raft_node_3

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
    raft_node_2._term = 2
    raft_node_3._change_state(RaftState.FOLLOWER)
    raft_node_3._voted_for = 42

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())

    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._state == RaftState.FOLLOWER
    assert raft_node_1._voted_for == 42

    assert raft_node_2._prev_state == RaftState.CANDIDATE
    assert raft_node_2._state == RaftState.LEADER
    assert raft_node_2._voted_for == 42

    assert raft_node_3._prev_state == RaftState.FOLLOWER
    assert raft_node_3._state == RaftState.FOLLOWER
    assert raft_node_3._voted_for == 42

    assert raft_node_2._term >= raft_node_1._term
    assert raft_node_2._term >= raft_node_3._term

    assert False


async def _unittest_raft_leader_election_2() -> None:
    def reset_node(node: RaftNode) -> None:
        """
        This will reset the node. This is useful for testing and debugging.
        """
        node.prev_state: RaftState = RaftState.FOLLOWER
        node.state: RaftState = RaftState.FOLLOWER
        node.current_term_timestamp: float = time.time()
        node.last_message_timestamp: float = time.time()
        node.current_term = 0
        node.voted_for = None

    #### SETUP ####
    logging.root.setLevel(logging.INFO)

    # start new raft nodes
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node_1 = RaftNode()
    os.environ["UAVCAN__NODE__ID"] = "42"
    raft_node_2 = RaftNode()
    os.environ["UAVCAN__NODE__ID"] = "43"
    raft_node_3 = RaftNode()

    # make all part of the same cluster
    raft_node_1.cluster = [raft_node_1, raft_node_2, raft_node_3]
    raft_node_2.cluster = [raft_node_1, raft_node_2, raft_node_3]
    raft_node_3.cluster = [raft_node_1, raft_node_2, raft_node_3]

    # setup election and term timeout
    election_timeout = 2
    term_timeout = 1
    raft_node_1.election_timeout = election_timeout
    raft_node_2.election_timeout = election_timeout
    raft_node_3.election_timeout = election_timeout + 2
    raft_node_1.term_timeout = term_timeout
    raft_node_2.term_timeout = term_timeout
    raft_node_3.term_timeout = term_timeout

    #### TEST STAGE 8/9 ####

    raft_node_1.prev_state = RaftState.CANDIDATE
    raft_node_1.state = RaftState.LEADER
    raft_node_1.current_term = 5
    raft_node_1.voted_for = 41

    raft_node_2.prev_state = RaftState.CANDIDATE
    raft_node_2.state = RaftState.LEADER
    raft_node_2.current_term = (
        raft_node_1.current_term + 2
    )  # higher term than node 1, +1 doesn't work because of election timeout increasing term of node 1
    raft_node_2.voted_for = 42

    raft_node_3.prev_state = RaftState.FOLLOWER
    raft_node_3.state = RaftState.FOLLOWER
    raft_node_3.current_term = 0
    raft_node_3.voted_for = None

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())

    await asyncio.sleep(election_timeout)

    assert raft_node_1.prev_state == RaftState.LEADER
    assert raft_node_1.state == RaftState.FOLLOWER
    # assert raft_node_1.current_term == 6
    assert raft_node_1.voted_for == 42

    assert raft_node_2.prev_state == RaftState.CANDIDATE
    assert raft_node_2.state == RaftState.LEADER
    # assert raft_node_2.current_term == 6
    assert raft_node_2.voted_for == 42

    assert raft_node_3.prev_state == RaftState.FOLLOWER
    assert raft_node_3.state == RaftState.FOLLOWER
    # assert raft_node_3.current_term == 6
    assert raft_node_3.voted_for == 42

    assert raft_node_2.current_term >= raft_node_1.current_term
    assert raft_node_2.current_term >= raft_node_3.current_term

    #### TEST STAGE 10 ####
    _logger.info("TEST STAGE 10")

    reset_node(raft_node_1)
    reset_node(raft_node_2)
    reset_node(raft_node_3)

    raft_node_1.prev_state = raft_node_1.state
    raft_node_1.state = RaftState.CANDIDATE

    raft_node_2.prev_state = RaftState.CANDIDATE
    raft_node_2.state = RaftState.LEADER

    raft_node_3.prev_state = RaftState.FOLLOWER
    raft_node_3.state = RaftState.FOLLOWER

    _logger.info("raft node 1 prev_state: %s", raft_node_1.prev_state)
    _logger.info("raft node 1 state: %s", raft_node_1.state)

    await asyncio.sleep(election_timeout)

    assert raft_node_1.prev_state == RaftState.CANDIDATE
    assert raft_node_1.state == RaftState.FOLLOWER
    # assert raft_node_1.current_term == 6
    assert raft_node_1.voted_for == 42

    assert raft_node_2.prev_state == RaftState.CANDIDATE
    assert raft_node_2.state == RaftState.LEADER
    # assert raft_node_2.current_term == 6
    assert raft_node_2.voted_for == 42

    assert raft_node_3.prev_state == RaftState.FOLLOWER
    assert raft_node_3.state == RaftState.FOLLOWER
    # assert raft_node_3.current_term == 6
    assert raft_node_3.voted_for == 42

    assert raft_node_2.current_term >= raft_node_1.current_term
    assert raft_node_2.current_term >= raft_node_3.current_term

    assert False

    # stop tasks
    # tasks = asyncio.all_tasks()
    # for task in tasks:
    #     task.cancel()

    # QUESTION: all these "Task was destroyed but it is pending!" warnings?
