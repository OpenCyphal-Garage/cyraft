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


async def _unittest_raft_leader_election() -> None:
    """
    Test the Raft Leader Election Algorithm

    - at any given time each server is in one of three states: leader, follower or candidate
    - state transitions:
        - follower
            - start as a follower (test 1)
            - if election timeout, convert to candidate and start election (test 2)
        - candidate
            - if votes received from majority of servers: become leader (test 3)
            - if election timeout elapses: start new election (test 4)
            - discovers current leader or new term: convert to follower
        - leader
            - discovers current leader with higher term: convert to follower
    """
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

    # test 1: all nodes start as follower
    assert raft_node_1.state == RaftState.FOLLOWER
    assert raft_node_2.state == RaftState.FOLLOWER
    assert raft_node_3.state == RaftState.FOLLOWER

    # setup election timeout
    election_timeout = 0.1
    raft_node_1_election_timeout = election_timeout
    raft_node_2_election_timeout = election_timeout + 1
    raft_node_3_election_timeout = election_timeout + 2
    raft_node_1.set_election_timeout(raft_node_1_election_timeout)
    raft_node_2.set_election_timeout(raft_node_2_election_timeout)
    raft_node_3.set_election_timeout(raft_node_3_election_timeout)

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())
    asyncio.create_task(raft_node_3.run())
    # let run until election timeout
    await asyncio.sleep(election_timeout)

    # test 2 and 3: if election timeout, convert to candidate and start election, elect leader
    assert raft_node_1.prev_state == RaftState.CANDIDATE
    assert raft_node_1.state == RaftState.LEADER
    assert raft_node_2.state == RaftState.FOLLOWER
    assert raft_node_3.state == RaftState.FOLLOWER

    assert raft_node_1.voted_for == 41
    assert raft_node_2.voted_for == 41
    assert raft_node_3.voted_for == 41

    raft_node_1.close()
    raft_node_2.close()
    raft_node_3.close()

    # stop tasks
    tasks = asyncio.all_tasks()
    for task in tasks:
        task.cancel()

    # QUESTION: all these "Task was destroyed but it is pending!" warnings?
