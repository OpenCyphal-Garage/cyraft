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
# Get the absolute path of the parent directory
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Add the parent directory to the Python path
sys.path.append(parent_dir)
# This can be removed if setting PYTHONPATH (export PYTHONPATH=cyraft)

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
    # assert raft_node._match_index == []

    # Adding a single node (same node id, so should not be added)
    raft_node.add_remote_node(41)
    assert len(raft_node._cluster) == 0
    assert len(raft_node._request_vote_clients) == 0
    assert len(raft_node._append_entries_clients) == 0
    assert raft_node._next_index == []
    # assert raft_node._match_index == []

    # Adding a single node (different node id, so should be added)
    raft_node.add_remote_node(42)
    assert len(raft_node._cluster) == 1
    assert raft_node._cluster[0] == 42
    assert len(raft_node._request_vote_clients) == 1
    assert len(raft_node._append_entries_clients) == 1
    assert raft_node._next_index == [1]
    # assert raft_node._match_index == [0]

    # Removing a single node (should become empty cluster)
    raft_node.remove_remote_node(42)
    assert len(raft_node._cluster) == 0
    assert len(raft_node._request_vote_clients) == 0
    assert len(raft_node._append_entries_clients) == 0
    assert raft_node._next_index == []
    # assert raft_node._match_index == []

    # Adding multiple nodes
    raft_node.add_remote_node([42, 43])
    assert len(raft_node._cluster) == 2
    assert raft_node._cluster[0] == 42
    assert raft_node._cluster[1] == 43
    assert len(raft_node._request_vote_clients) == 2
    assert len(raft_node._append_entries_clients) == 2
    assert raft_node._next_index == [1, 1]
    # assert raft_node._match_index == [0, 0]

    # Remove one of the nodes, make sure other one is still there
    raft_node.remove_remote_node(42)
    assert len(raft_node._cluster) == 1
    assert raft_node._cluster[0] == 43
    assert len(raft_node._request_vote_clients) == 1
    assert len(raft_node._append_entries_clients) == 1
    assert raft_node._next_index == [1]
    # assert raft_node._match_index == [0]


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

    asyncio.create_task(raft_node.run())
    raft_node._change_state(RaftState.CANDIDATE)
    raft_node._change_state(RaftState.LEADER)  # only leader can increase term

    await asyncio.sleep(TERM_TIMEOUT)  # + 0.1 to make sure the timer has been reset
    assert raft_node._term == 1
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node._term == 2
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node._term == 3

    raft_node._change_state(RaftState.FOLLOWER)  # follower should not increase term
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node._term == 3

    raft_node.close()
    await asyncio.sleep(1)  # give some time for the node to close


async def _unittest_raft_node_election_timeout() -> None:
    """
    Test that the node converts to CANDIDATE after the election timeout
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    ELECTION_TIMEOUT = 5
    TERM_TIMEOUT = 1
    raft_node.election_timeout = ELECTION_TIMEOUT
    raft_node.term_timeout = TERM_TIMEOUT

    assert raft_node._state == RaftState.FOLLOWER
    asyncio.create_task(raft_node.run())

    await asyncio.sleep(ELECTION_TIMEOUT + 0.1)
    assert raft_node._prev_state == RaftState.CANDIDATE
    assert raft_node._state == RaftState.LEADER

    assert raft_node._term == 1  # term should be increased due to starting election

    raft_node.close()
    await asyncio.sleep(1)  # give some time for the node to close


async def _unittest_raft_node_heartbeat() -> None:
    """
    Test that the node does NOT convert to candidate if it receives a heartbeat message
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    ELECTION_TIMEOUT = 5
    TERM_TIMEOUT = 1
    raft_node.election_timeout = ELECTION_TIMEOUT
    raft_node.term_timeout = TERM_TIMEOUT
    raft_node._voted_for = 42

    asyncio.create_task(raft_node.run())
    await asyncio.sleep(ELECTION_TIMEOUT * 0.90)  # sleep until right before election timeout

    # send heartbeat
    terms_passed = raft_node._term  # leader's term is equal to the follower's term
    await raft_node._serve_append_entries(
        sirius_cyber_corp.AppendEntries_1.Request(
            term=terms_passed,  # leader's term
            prev_log_index=0,  # index of log entry immediately preceding new ones
            prev_log_term=0,  # term of prevLogIndex entry
            leader_commit=0,  # leader's commitIndex
            log_entry=None,  # log entries to store (empty for heartbeat)
        ),
        pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,  # leader's node id
            timestamp=time.time(),  # leader's timestamp
            priority=0,  # leader's priority
            transfer_id=0,  # leader's transfer id
        ),
    )

    # wait for heartbeat to be processed [election is reached but shouldn't become leader due to heartbeat]
    await asyncio.sleep(ELECTION_TIMEOUT * 0.1 + 0.1)
    assert raft_node._state == RaftState.FOLLOWER
    assert raft_node._voted_for == 42

    # send heartbeat again
    # (this time leader has a higher term, we want to make sure that the follower's term is updated)
    await asyncio.sleep(ELECTION_TIMEOUT * 0.90)  # sleep until right before election timeout
    terms_passed = raft_node._term + 5  # leader's term is higher than the follower's term
    await raft_node._serve_append_entries(
        sirius_cyber_corp.AppendEntries_1.Request(
            term=terms_passed,  # leader's term
            prev_log_index=0,  # index of log entry immediately preceding new ones
            prev_log_term=0,  # term of prevLogIndex entry
            leader_commit=0,  # leader's commitIndex
            log_entry=None,  # log entries to store (empty for heartbeat)
        ),
        pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,  # leader's node id
            timestamp=time.time(),  # leader's timestamp
            priority=0,  # leader's priority
            transfer_id=0,  # leader's transfer id
        ),
    )

    # wait for heartbeat to be processed [election is reached but shouldn't become leader due to heartbeat]
    await asyncio.sleep(ELECTION_TIMEOUT * 0.1 + 0.1)
    assert raft_node._state == RaftState.FOLLOWER
    assert raft_node._voted_for == 42
    assert raft_node._term == terms_passed

    # send heartbeat again
    # (this time from a different leader with a higher term, we want to make sure the follower switches leader and updates term)
    await asyncio.sleep(ELECTION_TIMEOUT * 0.90)  # sleep until right before election timeout
    terms_passed = raft_node._term + 5  # leader's term is higher than the follower's term
    await raft_node._serve_append_entries(
        sirius_cyber_corp.AppendEntries_1.Request(
            term=terms_passed,  # leader's term
            prev_log_index=0,  # index of log entry immediately preceding new ones
            prev_log_term=0,  # term of prevLogIndex entry
            leader_commit=0,  # leader's commitIndex
            log_entry=None,  # log entries to store (empty for heartbeat)
        ),
        pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=43,  # leader's node id (different from previous leader)
            timestamp=time.time(),  # leader's timestamp
            priority=0,  # leader's priority
            transfer_id=0,  # leader's transfer id
        ),
    )

    # wait for heartbeat to be processed [election is reached but shouldn't become leader due to heartbeat]
    await asyncio.sleep(ELECTION_TIMEOUT * 0.1 + 0.1)
    assert raft_node._state == RaftState.FOLLOWER
    assert raft_node._voted_for == 43
    assert raft_node._term == terms_passed

    # send heartbeat again
    # (this time the leader's term is lower than the follower's term, we want to make sure the follower doesn't switch leader)
    await asyncio.sleep(ELECTION_TIMEOUT * 0.90)  # sleep until right before election timeout
    terms_passed = raft_node._term - 1  # leader's term is lower than the follower's term
    await raft_node._serve_append_entries(
        sirius_cyber_corp.AppendEntries_1.Request(
            term=terms_passed,  # leader's term
            prev_log_index=0,  # index of log entry immediately preceding new ones
            prev_log_term=0,  # term of prevLogIndex entry
            leader_commit=0,  # leader's commitIndex
            log_entry=None,  # log entries to store (empty for heartbeat)
        ),
        pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,  # old leader's node id, which has lower term
            timestamp=time.time(),  # leader's timestamp
            priority=0,  # leader's priority
            transfer_id=0,  # leader's transfer id
        ),
    )

    ## test that the node converts to candidate after the election timeout [no valid heartbeat is received]
    await asyncio.sleep(ELECTION_TIMEOUT * 0.1 + 0.1)
    assert raft_node._prev_state == RaftState.CANDIDATE

    raft_node.close()
    await asyncio.sleep(1)  # fixes when just running this test, however not when "pytest /cyraft" is run


async def _unittest_raft_node_request_vote_rpc() -> None:
    """
    Test the _serve_request_vote() method
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    assert raft_node._state == RaftState.FOLLOWER
    raft_node._term = 3

    # test 1: vote not granted if already voted for another candidate in this term
    follower_term = raft_node._term
    raft_node._voted_for = 43  # node voted for another candidate

    request = sirius_cyber_corp.RequestVote_1.Request(
        term=follower_term,  # candidate's term is equal follower node term
        last_log_index=0,  # index of candidate's last log entry
        last_log_term=0,  # term of candidate's last log entry
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,  # candidate's node id
        timestamp=0,
        priority=0,
        transfer_id=0,
    )

    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node._voted_for == 43
    assert response.vote_granted == False

    # test 2: vote not granted if follower node term is greater than candidate's term
    raft_node._voted_for = None  # node has not voted for any candidate
    raft_node._term = request.term + 1

    assert request.term < raft_node._term  # follower node term is greater than candidate's term
    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node._voted_for == None
    assert response.vote_granted == False

    # test 3: vote granted if not voted for another candidate
    #         and the candidate's term is GREATER than the node's term
    raft_node._voted_for = None
    raft_node._term = request.term - 1

    assert request.term > raft_node._term  # follower node term is less than candidate's term
    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node._voted_for == 42
    assert response.vote_granted == True
    assert raft_node._term == request.term  # follower node term is updated to candidate's term

    # test 4: vote granted if not voted for another candidate
    #         and the candidate's term is EQUAL to the node's term
    raft_node._voted_for = None
    raft_node._term = request.term - 1

    assert request.term > raft_node._term  # follower node term is less than candidate's term
    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node._voted_for == 42
    assert response.vote_granted == True
    assert raft_node._term == request.term  # follower node term is updated to candidate's term


async def _unittest_raft_node_start_election() -> None:
    """
    Test the _start_election method

    Using two nodes, one has a shorter election timeout than the other.
    The node with the shorter election timeout should become the leader.
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__CLN__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"
    os.environ["UAVCAN__CLN__APPEND_ENTRIES__ID"] = "2"
    ELECTION_TIMEOUT = 5
    raft_node_1 = RaftNode()
    raft_node_1.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "42"
    raft_node_2 = RaftNode()
    raft_node_2.election_timeout = ELECTION_TIMEOUT * 2

    cluster = [raft_node_1._node.id, raft_node_2._node.id]
    assert cluster == [41, 42]
    raft_node_1.add_remote_node(cluster)
    raft_node_2.add_remote_node(cluster)

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())

    await asyncio.sleep(ELECTION_TIMEOUT + 1)

    # test 1: node 1 should become candidate
    assert raft_node_1._state == RaftState.LEADER
    assert raft_node_1._prev_state == RaftState.CANDIDATE
    assert raft_node_1._voted_for == 41

    # test 2: node 2 should become follower
    assert raft_node_2._state == RaftState.FOLLOWER
    assert raft_node_2._voted_for == 41

    # test 3: node 1 should send a heartbeat to node 2, remain leader
    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1._state == RaftState.LEADER

    raft_node_1.close()
    raft_node_2.close()
    await asyncio.sleep(1)


async def _unittest_raft_node_append_entries_rpc() -> None:
    """
    Test the _serve_append_entries method

     Step 1: Append 3 log entries
       ____________ ____________ ____________ ____________
      | 0          | 1          | 2          | 3          |     Log index
      | 0          | 4          | 5          | 6          |     Log term
      | empty <= 0 | top_1 <= 7 | top_2 <= 8 | top_3 <= 9 |     Name <= value
      |____________|____________|____________|____________|

     Step 2: Replace log entry 3 with a new entry
       ____________
      | 3          |     Log index
      | 7          |     Log term
      | top_3 <= 10|     Name <= value
      |____________|

     Step 3: Replace log entries 2 and 3 with new entries
       ____________ ____________
      | 2          | 3          |     Log index
      | 8          | 9          |     Log term
      | top_2 <= 11| top_3 <= 12|     Name <= value
      |____________|____________|

     Step 4: Add an already existing log entry
       ____________
      | 3          |     Log index
      | 9          |     Log term
      | top_3 <= 12|     Name <= value
      |____________|

     Step 5: Add an additional log entry
       ____________
      | 4          |     Log index
      | 10         |     Log term
      | top_4 <= 13|     Name <= value
      |____________|

     Result:
       ____________ ____________ ____________ ____________ ____________
      | 0          | 1          | 2          | 3          | 4          |     Log index
      | 0          | 4          | 8          | 9          | 10         |     Log term
      | empty <= 0 | top_1 <= 7 | top_2 <= 10| top_3 <= 11| top_4 <= 13|     Name <= value
      |____________|____________|____________|____________|____________|

     Step 6: Try to append old log entry (term < currentTerm)
       ____________
      | 4          |     Log index
      | 9          |     Log term
      | top_4 <= 14|     Name <= value
      |____________|

     Step 7: Try to append valid log entry, however entry at prev_log_index term does not match
       ____________
      | 4          |     Log index
      | 11         |     Log term
      | top_4 <= 15|     Name <= value
      |____________|


      Every step check:
        - self._term
        - self._voted_for
        - self._log
        - self._commit_index
    """

    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    raft_node._voted_for = 42

    _logger.info("================== TEST 1: append 3 log entries ==================")
    new_entries = [
        sirius_cyber_corp.LogEntry_1(
            term=4,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_1"),
                value=7,
            ),
        ),
        sirius_cyber_corp.LogEntry_1(
            term=5,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_2"),
                value=8,
            ),
        ),
        sirius_cyber_corp.LogEntry_1(
            term=6,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_3"),
                value=9,
            ),
        ),
    ]

    for index, new_entry in enumerate(new_entries):
        request = sirius_cyber_corp.AppendEntries_1.Request(
            term=6,
            prev_log_index=index,  # prev_log_index: 0, 1, 2
            prev_log_term=raft_node._log[index].term,
            log_entry=new_entry,
        )
        metadata = pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,
            timestamp=time.time(),
            priority=0,
            transfer_id=0,
        )
        response = await raft_node._serve_append_entries(request, metadata)
        assert response.success == True

    assert raft_node._term == 6
    assert raft_node._voted_for == 42

    assert len(raft_node._log) == 1 + 3
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node._log[0].entry.value == 0
    assert raft_node._log[1].term == 4
    assert raft_node._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node._log[1].entry.value == 7
    assert raft_node._log[2].term == 5
    assert raft_node._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node._log[2].entry.value == 8
    assert raft_node._log[3].term == 6
    assert raft_node._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node._log[3].entry.value == 9
    assert raft_node._commit_index == 3

    # Once https://github.com/OpenCyphal/pycyphal/issues/297 is fixed, we can do this (instead of the above):
    # assert raft_node.log[0] == index_zero_entry
    # assert raft_node.log[1] == new_entries[0]
    # assert raft_node.log[2] == new_entries[1]
    # assert raft_node.log[3] == new_entries[2]

    _logger.info("================== TEST 2: Replace log entry 3 with a new entry ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=7,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_3"),
            value=10,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=7,
        prev_log_index=2,  # index of top_2
        prev_log_term=raft_node._log[2].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node._serve_append_entries(request, metadata)
    assert response.success == True

    assert raft_node._term == 7
    assert raft_node._voted_for == 42

    assert len(raft_node._log) == 1 + 3
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node._log[0].entry.value == 0

    assert raft_node._log[1].term == 4
    assert raft_node._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node._log[1].entry.value == 7
    assert raft_node._log[2].term == 5
    assert raft_node._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node._log[2].entry.value == 8
    assert raft_node._log[3].term == 7
    assert raft_node._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node._log[3].entry.value == 10
    assert raft_node._commit_index == 3

    _logger.info("================== TEST 3: Replace log entries 2 and 3 with new entries ==================")

    new_entries = [
        sirius_cyber_corp.LogEntry_1(
            term=8,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_2"),
                value=11,
            ),
        ),
        sirius_cyber_corp.LogEntry_1(
            term=9,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_3"),
                value=12,
            ),
        ),
    ]

    for index, new_entry in enumerate(new_entries):
        request = sirius_cyber_corp.AppendEntries_1.Request(
            term=9,
            prev_log_index=index + 1,  # index: 1, 2
            prev_log_term=raft_node._log[index + 1].term,
            log_entry=new_entry,
        )
        metadata = pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,
            timestamp=time.time(),
            priority=0,
            transfer_id=0,
        )
        response = await raft_node._serve_append_entries(request, metadata)
        assert response.success == True

    assert raft_node._term == 9
    assert raft_node._voted_for == 42

    assert len(raft_node._log) == 1 + 3
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.value == 0
    assert raft_node._log[1].term == 4
    assert raft_node._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node._log[1].entry.value == 7
    assert raft_node._log[2].term == 8
    assert raft_node._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node._log[2].entry.value == 11
    assert raft_node._log[3].term == 9
    assert raft_node._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node._log[3].entry.value == 12

    _logger.info("================== TEST 4: Add an already existing log entry ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=9,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_3"),
            value=12,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=9,
        prev_log_index=2,  # index of top_2
        prev_log_term=raft_node._log[2].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )

    response = await raft_node._serve_append_entries(request, metadata)
    assert response.success == True

    assert raft_node._term == 9
    assert raft_node._voted_for == 42

    assert len(raft_node._log) == 1 + 3
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.value == 0
    assert raft_node._log[1].term == 4
    assert raft_node._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node._log[1].entry.value == 7
    assert raft_node._log[2].term == 8
    assert raft_node._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node._log[2].entry.value == 11
    assert raft_node._log[3].term == 9
    assert raft_node._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node._log[3].entry.value == 12

    _logger.info("================== TEST 5: Add an additional log entry ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=10,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_4"),
            value=13,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=10,
        prev_log_index=3,  # index of top_3
        prev_log_term=raft_node._log[3].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node._serve_append_entries(request, metadata)
    assert response.success == True

    assert raft_node._term == 10
    assert raft_node._voted_for == 42

    assert len(raft_node._log) == 1 + 4
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.value == 0
    assert raft_node._log[1].term == 4
    assert raft_node._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node._log[1].entry.value == 7
    assert raft_node._log[2].term == 8
    assert raft_node._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node._log[2].entry.value == 11
    assert raft_node._log[3].term == 9
    assert raft_node._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node._log[3].entry.value == 12
    assert raft_node._log[4].term == 10
    assert raft_node._log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node._log[4].entry.value == 13

    _logger.info("================== TEST 6: Try to append old log entry (term < currentTerm) ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=9,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_4"),
            value=14,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=9,
        prev_log_index=3,
        prev_log_term=raft_node._log[3].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node._serve_append_entries(request, metadata)
    assert response.success == False

    assert raft_node._term == 10
    assert raft_node._voted_for == 42

    assert len(raft_node._log) == 1 + 4
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.value == 0
    assert raft_node._log[1].term == 4
    assert raft_node._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node._log[1].entry.value == 7
    assert raft_node._log[2].term == 8
    assert raft_node._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node._log[2].entry.value == 11
    assert raft_node._log[3].term == 9
    assert raft_node._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node._log[3].entry.value == 12
    assert raft_node._log[4].term == 10
    assert raft_node._log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node._log[4].entry.value == 13

    _logger.info(
        "================== TEST 7: Try to append valid log entry, however prev_log_index term does not match =================="
    )

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=11,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_4"),
            value=15,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=11,
        prev_log_index=4,
        prev_log_term=raft_node._log[4].term - 1,  # term mismatch
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node._serve_append_entries(request, metadata)
    assert response.success == False

    assert raft_node._term == 10
    assert raft_node._voted_for == 42

    assert len(raft_node._log) == 1 + 4
    assert raft_node._log[0].term == 0
    assert raft_node._log[0].entry.value == 0
    assert raft_node._log[1].term == 4
    assert raft_node._log[1].entry.value == 7
    assert raft_node._log[2].term == 8
    assert raft_node._log[2].entry.value == 11
    assert raft_node._log[3].term == 9
    assert raft_node._log[3].entry.value == 12
    assert raft_node._log[4].term == 10
    assert raft_node._log[4].entry.value == 13
