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

# Add parent directory to Python path
sys.path.append(os.path.abspath("/Users/maksimdrachov/cyraft"))  # Q: how to make it relative?``
from cyraft import RaftNode
from cyraft import RaftState

_logger = logging.getLogger(__name__)


async def _unittest_raft_leader_election() -> None:
    """
    Test the Raft Leader Election Algorithm

    - at any given time each server is in one of three states: leader, follower or candidate
    - state transitions:
        - follower
            - start as a follower (1)
            - if election timeout, convert to candidate and start election (2)
        - candidate
            - if election timeout elapses: start new election (3)
            - if votes received from majority of servers: become leader (4)
            - discovers current leader or new term: convert to follower (5.1, 5.2)
        - leader
            - discovers current leader with higher term: convert to follower (6)

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
            Candidate                   Follower                      Follower              [8]
    ------------------------------------Election timeout---------------------------------
            Leader                      Follower                      Follower              [9]
    ====================================Hard (re)set=====================================
            Leader                      Leader (higher term than 1)   Follower              [10]
            |(6)                        |                             |
            V                           V                             V
            Follower                    Leader                        Follower              [11]
    ====================================Hard (re)set=====================================
            Candidate                   Leader                        Follower              [12]
            |(5.1)                      |                             |
            V                           V                             V
            Follower                    Leader                        Follower              [13]
    ====================================Hard (re)set=====================================
            Candidate                   Candidate (higher term)       Follower              [14]
            |(5.2)                      |                             |
            V                           V                             V
            Follower                    Leader                        Follower              [15]

    To check on every state transition:
    - prev_state
    - state
    - current_term
    - voted_for
    """

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
    election_timeout = 5
    term_timeout = 2.5
    raft_node_1_election_timeout = election_timeout
    raft_node_2_election_timeout = election_timeout + 1
    raft_node_3_election_timeout = election_timeout + 2
    raft_node_1.set_election_timeout(raft_node_1_election_timeout)
    raft_node_2.set_election_timeout(raft_node_2_election_timeout)
    raft_node_3.set_election_timeout(raft_node_3_election_timeout)
    raft_node_1.set_term_timeout(term_timeout)
    raft_node_2.set_term_timeout(term_timeout)
    raft_node_3.set_term_timeout(term_timeout)

    #### TEST STAGE 1 ####

    assert raft_node_1.prev_state == RaftState.FOLLOWER
    assert raft_node_1.state == RaftState.FOLLOWER
    assert raft_node_1.current_term == 0
    assert raft_node_1.voted_for is None

    assert raft_node_2.prev_state == RaftState.FOLLOWER
    assert raft_node_2.state == RaftState.FOLLOWER
    assert raft_node_2.current_term == 0
    assert raft_node_2.voted_for is None

    assert raft_node_3.prev_state == RaftState.FOLLOWER
    assert raft_node_3.state == RaftState.FOLLOWER
    assert raft_node_3.current_term == 0
    assert raft_node_3.voted_for is None

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())
    # let run until election timeout
    await asyncio.sleep(election_timeout)

    #### TEST STAGE 2/3 ####

    assert raft_node_1.prev_state == RaftState.CANDIDATE
    assert raft_node_1.state == RaftState.LEADER
    # assert raft_node_1.current_term == 3  # +2 because of the term timeout, +1 because of the election timeout
    assert raft_node_1.voted_for == 41

    assert raft_node_2.prev_state == RaftState.FOLLOWER
    assert raft_node_2.state == RaftState.FOLLOWER
    # assert raft_node_2.current_term == 3  # term set by vote request
    assert raft_node_2.voted_for == 41

    assert raft_node_3.prev_state == RaftState.FOLLOWER
    assert raft_node_3.state == RaftState.FOLLOWER
    # assert raft_node_3.current_term == 3  # term set by vote request
    assert raft_node_3.voted_for == 41

    # assert raft_node_1.current_term >= 3
    assert raft_node_1.current_term >= raft_node_2.current_term
    assert raft_node_1.current_term >= raft_node_3.current_term

    #### TEST STAGE 4 ####

    await asyncio.sleep(election_timeout)

    assert raft_node_1.prev_state == RaftState.CANDIDATE
    assert raft_node_1.state == RaftState.LEADER
    # assert raft_node_1.current_term == 5  # +2 because of the term timeout
    assert raft_node_1.voted_for == 41

    assert raft_node_2.prev_state == RaftState.FOLLOWER
    assert raft_node_2.state == RaftState.FOLLOWER
    # assert raft_node_2.current_term == 5  # term set by heartbeat process
    assert raft_node_2.voted_for == 41

    assert raft_node_3.prev_state == RaftState.FOLLOWER
    assert raft_node_3.state == RaftState.FOLLOWER
    # assert raft_node_3.current_term == 5  # term set by heartbeat process
    assert raft_node_3.voted_for == 41

    assert raft_node_1.current_term >= raft_node_2.current_term
    assert raft_node_1.current_term >= raft_node_3.current_term

    assert False

    #### TEST STAGE 5 ####

    raft_node_1.close()
    raft_node_2.close()
    raft_node_3.close()

    assert False

    # stop tasks
    # tasks = asyncio.all_tasks()
    # for task in tasks:
    #     task.cancel()

    # QUESTION: all these "Task was destroyed but it is pending!" warnings?
