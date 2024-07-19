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

"""
Test the Raft log replication. The steps are the same as in raft_node.py, but this time
are executing on a cluster of nodes, so we need to make sure the log is replicated across
all nodes.

 Step 1: Append 3 log entries
   ____________ ____________ ____________ ____________
  | 0          | 1          | 2          | 3          |     Log index
  | 0          | 0          | 0          | 0          |     Log term
  | empty <= 0 | top_1 <= 7 | top_2 <= 8 | top_3 <= 9 |     Name <= value
  |____________|____________|____________|____________|

 Step 2: Replace log entry 3 with a new entry
   ____________
  | 3          |     Log index
  | 1          |     Log term
  | top_3 <= 10|     Name <= value
  |____________|

 Step 3: Replace log entries 2 and 3 with new entries
   ____________ ____________
  | 2          | 3          |     Log index
  | 1          | 1          |     Log term
  | top_2 <= 11| top_3 <= 12|     Name <= value
  |____________|____________|

 Step 4: Add an already existing log entry
   ____________
  | 3          |     Log index
  | 1          |     Log term
  | top_3 <= 12|     Name <= value
  |____________|

 Step 5: Add an additional log entry
   ____________
  | 4          |     Log index
  | 1          |     Log term
  | top_4 <= 13|     Name <= value
  |____________|

 Result:
   ____________ ____________ ____________ ____________ ____________
  | 0          | 1          | 2          | 3          | 4          |     Log index
  | 0          | 0          | 1          | 1          | 1          |     Log term
  | empty <= 0 | top_1 <= 7 | top_2 <= 10| top_3 <= 11| top_4 <= 13|     Name <= value
  |____________|____________|____________|____________|____________|

 Step 6: Try to append old log entry (term < currentTerm)
   ____________
  | 4          |     Log index
  | 0          |     Log term
  | top_4 <= 14|     Name <= value
  |____________|

 Step 7: Try to append valid log entry, however entry at prev_log_index term does not match
   ____________
  | 4          |     Log index
  | 1          |     Log termы
  | top_4 <= 15|     Name <= value
  |____________|
"""


async def _unittest_raft_log_replication() -> None:
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

    _logger.info("================== TEST 1: append 3 log entries ==================")
    new_entries = [
        sirius_cyber_corp.LogEntry_1(
            term=0,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_1"),
                value=7,
            ),
        ),
        sirius_cyber_corp.LogEntry_1(
            term=0,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_2"),
                value=8,
            ),
        ),
        sirius_cyber_corp.LogEntry_1(
            term=0,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_3"),
                value=9,
            ),
        ),
    ]

    for index, new_entry in enumerate(new_entries):
        request = sirius_cyber_corp.AppendEntries_1.Request(
            term=raft_node_1._term,
            prev_log_index=index,  # prev_log_index: 0, 1, 2
            prev_log_term=raft_node_1._log[index].term,
            log_entry=new_entry,
        )
        metadata = pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,
            timestamp=time.time(),
            priority=0,
            transfer_id=0,
        )
        response = await raft_node_1._serve_append_entries(request, metadata)
        assert response.success == True

    # wait for the request to be replicated
    await asyncio.sleep(TERM_TIMEOUT + 1)

    # check if the new entry is replicated in the leader node
    assert len(raft_node_1._log) == 1 + 3
    assert raft_node_1._log[0].term == 0
    assert raft_node_1._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node_1._log[0].entry.value == 0
    assert raft_node_1._log[1].term == 0
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 7
    assert raft_node_1._log[2].term == 0
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_1._log[2].entry.value == 8
    assert raft_node_1._log[3].term == 0
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_1._log[3].entry.value == 9
    assert raft_node_1._commit_index == 3

    # check if the new entry is replicated in the follower nodes
    assert len(raft_node_2._log) == 1 + 3
    assert raft_node_2._log[0].term == 0
    assert raft_node_2._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node_2._log[0].entry.value == 0
    assert raft_node_2._log[1].term == 0
    assert raft_node_2._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_2._log[1].entry.value == 7
    assert raft_node_2._log[2].term == 0
    assert raft_node_2._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_2._log[2].entry.value == 8
    assert raft_node_2._log[3].term == 0
    assert raft_node_2._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_2._log[3].entry.value == 9
    assert raft_node_2._commit_index == 3
    assert len(raft_node_3._log) == 1 + 3
    assert raft_node_3._log[0].term == 0
    assert raft_node_3._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node_3._log[0].entry.value == 0
    assert raft_node_3._log[1].term == 0
    assert raft_node_3._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_3._log[1].entry.value == 7
    assert raft_node_3._log[2].term == 0
    assert raft_node_3._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_3._log[2].entry.value == 8
    assert raft_node_3._log[3].term == 0
    assert raft_node_3._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_3._log[3].entry.value == 9
    assert raft_node_3._commit_index == 3

    _logger.info("================== TEST 2: Replace log entry 3 with a new entry ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=1,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_3"),
            value=10,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=1,
        prev_log_index=2,  # index of top_2
        prev_log_term=raft_node_1._log[2].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node_1._serve_append_entries(request, metadata)
    assert response.success == True

    await asyncio.sleep(TERM_TIMEOUT + 1)

    assert len(raft_node_1._log) == 1 + 3
    assert raft_node_1._log[0].term == 0
    assert raft_node_1._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node_1._log[0].entry.value == 0
    assert raft_node_1._log[1].term == 0
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 7
    assert raft_node_1._log[2].term == 0
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_1._log[2].entry.value == 8
    assert raft_node_1._log[3].term == 1
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_1._log[3].entry.value == 10
    assert raft_node_1._commit_index == 3

    # check if the new entry is replicated in the follower nodes
    assert len(raft_node_2._log) == 1 + 3
    assert raft_node_2._log[0].term == 0
    assert raft_node_2._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node_2._log[0].entry.value == 0
    assert raft_node_2._log[1].term == 0
    assert raft_node_2._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_2._log[1].entry.value == 7
    assert raft_node_2._log[2].term == 0
    assert raft_node_2._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_2._log[2].entry.value == 8
    assert raft_node_2._log[3].term == 1
    assert raft_node_2._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_2._log[3].entry.value == 10
    assert raft_node_2._commit_index == 3
    assert len(raft_node_3._log) == 1 + 3
    assert raft_node_3._log[0].term == 0
    assert raft_node_3._log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node_3._log[0].entry.value == 0
    assert raft_node_3._log[1].term == 0
    assert raft_node_3._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_3._log[1].entry.value == 7
    assert raft_node_3._log[2].term == 0
    assert raft_node_3._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_3._log[2].entry.value == 8
    assert raft_node_3._log[3].term == 1
    assert raft_node_3._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_3._log[3].entry.value == 10
    assert raft_node_3._commit_index == 3

    _logger.info("================== TEST 3: Replace log entries 2 and 3 with new entries ==================")

    new_entries = [
        sirius_cyber_corp.LogEntry_1(
            term=1,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_2"),
                value=11,
            ),
        ),
        sirius_cyber_corp.LogEntry_1(
            term=1,
            entry=sirius_cyber_corp.Entry_1(
                name=uavcan.primitive.String_1(value="top_3"),
                value=12,
            ),
        ),
    ]

    for index, new_entry in enumerate(new_entries):
        request = sirius_cyber_corp.AppendEntries_1.Request(
            term=raft_node_1._term,
            prev_log_index=index + 1,  # index: 1, 2
            prev_log_term=raft_node_1._log[index + 1].term,
            log_entry=new_entry,
        )
        metadata = pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,
            timestamp=time.time(),
            priority=0,
            transfer_id=0,
        )
        response = await raft_node_1._serve_append_entries(request, metadata)
        assert response.success == True

    await asyncio.sleep(TERM_TIMEOUT + 1)

    assert len(raft_node_1._log) == 1 + 3
    assert raft_node_1._log[0].term == 0
    assert raft_node_1._log[0].entry.value == 0
    assert raft_node_1._log[1].term == 0
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 7
    assert raft_node_1._log[2].term == 1
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_1._log[2].entry.value == 11
    assert raft_node_1._log[3].term == 1
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_1._log[3].entry.value == 12

    # check if the new entries are replicated in the follower nodes
    assert len(raft_node_2._log) == 1 + 3
    assert raft_node_2._log[0].term == 0
    assert raft_node_2._log[0].entry.value == 0
    assert raft_node_2._log[1].term == 0
    assert raft_node_2._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_2._log[1].entry.value == 7
    assert raft_node_2._log[2].term == 1
    assert raft_node_2._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_2._log[2].entry.value == 11
    assert raft_node_2._log[3].term == 1
    assert raft_node_2._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_2._log[3].entry.value == 12
    assert len(raft_node_3._log) == 1 + 3
    assert raft_node_3._log[0].term == 0
    assert raft_node_3._log[0].entry.value == 0
    assert raft_node_3._log[1].term == 0
    assert raft_node_3._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_3._log[1].entry.value == 7
    assert raft_node_3._log[2].term == 1
    assert raft_node_3._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_3._log[2].entry.value == 11
    assert raft_node_3._log[3].term == 1
    assert raft_node_3._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_3._log[3].entry.value == 12

    _logger.info("================== TEST 4: Add an already existing log entry ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=1,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_3"),
            value=12,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=raft_node_1._term,
        prev_log_index=2,  # index of top_2
        prev_log_term=raft_node_1._log[2].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )

    response = await raft_node_1._serve_append_entries(request, metadata)
    assert response.success == True

    await asyncio.sleep(TERM_TIMEOUT + 1)

    assert len(raft_node_1._log) == 1 + 3
    assert raft_node_1._log[0].term == 0
    assert raft_node_1._log[0].entry.value == 0
    assert raft_node_1._log[1].term == 0
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 7
    assert raft_node_1._log[2].term == 1
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_1._log[2].entry.value == 11
    assert raft_node_1._log[3].term == 1
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_1._log[3].entry.value == 12

    # check if the new entries are replicated in the follower nodes
    assert len(raft_node_2._log) == 1 + 3
    assert raft_node_2._log[0].term == 0
    assert raft_node_2._log[0].entry.value == 0
    assert raft_node_2._log[1].term == 0
    assert raft_node_2._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_2._log[1].entry.value == 7
    assert raft_node_2._log[2].term == 1
    assert raft_node_2._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_2._log[2].entry.value == 11
    assert raft_node_2._log[3].term == 1
    assert raft_node_2._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_2._log[3].entry.value == 12
    assert len(raft_node_3._log) == 1 + 3
    assert raft_node_3._log[0].term == 0
    assert raft_node_3._log[0].entry.value == 0
    assert raft_node_3._log[1].term == 0
    assert raft_node_3._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_3._log[1].entry.value == 7
    assert raft_node_3._log[2].term == 1
    assert raft_node_3._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_3._log[2].entry.value == 11
    assert raft_node_3._log[3].term == 1
    assert raft_node_3._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_3._log[3].entry.value == 12

    _logger.info("================== TEST 5: Add an additional log entry ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=1,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_4"),
            value=13,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=raft_node_1._term,
        prev_log_index=3,  # index of top_3
        prev_log_term=raft_node_1._log[3].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node_1._serve_append_entries(request, metadata)
    assert response.success == True

    await asyncio.sleep(TERM_TIMEOUT + 1)

    assert len(raft_node_1._log) == 1 + 4
    assert raft_node_1._log[0].term == 0
    assert raft_node_1._log[0].entry.value == 0
    assert raft_node_1._log[1].term == 0
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 7
    assert raft_node_1._log[2].term == 1
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_1._log[2].entry.value == 11
    assert raft_node_1._log[3].term == 1
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_1._log[3].entry.value == 12
    assert raft_node_1._log[4].term == 1
    assert raft_node_1._log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node_1._log[4].entry.value == 13

    _logger.info("================== TEST 6: Try to append old log entry (term < currentTerm) ==================")

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=0,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_4"),
            value=14,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=raft_node_1._term - 1,  # term < currentTerm
        prev_log_index=3,
        prev_log_term=raft_node_1._log[3].term,
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node_1._serve_append_entries(request, metadata)
    assert response.success == False

    await asyncio.sleep(TERM_TIMEOUT + 1)

    assert len(raft_node_1._log) == 1 + 4
    assert raft_node_1._log[0].term == 0
    assert raft_node_1._log[0].entry.value == 0
    assert raft_node_1._log[1].term == 0
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 7
    assert raft_node_1._log[2].term == 1
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_1._log[2].entry.value == 11
    assert raft_node_1._log[3].term == 1
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_1._log[3].entry.value == 12
    assert raft_node_1._log[4].term == 1
    assert raft_node_1._log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node_1._log[4].entry.value == 13

    assert len(raft_node_2._log) == 1 + 4
    assert raft_node_2._log[0].term == 0
    assert raft_node_2._log[0].entry.value == 0
    assert raft_node_2._log[1].term == 0
    assert raft_node_2._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_2._log[1].entry.value == 7
    assert raft_node_2._log[2].term == 1
    assert raft_node_2._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_2._log[2].entry.value == 11
    assert raft_node_2._log[3].term == 1
    assert raft_node_2._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_2._log[3].entry.value == 12
    assert raft_node_2._log[4].term == 1
    assert raft_node_2._log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node_2._log[4].entry.value == 13
    assert len(raft_node_3._log) == 1 + 4
    assert raft_node_3._log[0].term == 0
    assert raft_node_3._log[0].entry.value == 0
    assert raft_node_3._log[1].term == 0
    assert raft_node_3._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_3._log[1].entry.value == 7
    assert raft_node_3._log[2].term == 1
    assert raft_node_3._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_3._log[2].entry.value == 11
    assert raft_node_3._log[3].term == 1
    assert raft_node_3._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_3._log[3].entry.value == 12
    assert raft_node_3._log[4].term == 1
    assert raft_node_3._log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node_3._log[4].entry.value == 13

    _logger.info(
        "================== TEST 7: Try to append valid log entry, however prev_log_index term does not match =================="
    )

    new_entry = sirius_cyber_corp.LogEntry_1(
        term=1,
        entry=sirius_cyber_corp.Entry_1(
            name=uavcan.primitive.String_1(value="top_5"),
            value=15,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=raft_node_1._term,
        prev_log_index=4,
        prev_log_term=raft_node_1._log[4].term - 1,  # term mismatch
        log_entry=new_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    response = await raft_node_1._serve_append_entries(request, metadata)
    assert response.success == False

    await asyncio.sleep(TERM_TIMEOUT + 1)

    assert len(raft_node_1._log) == 1 + 4
    assert raft_node_1._log[0].term == 0
    assert raft_node_1._log[0].entry.value == 0
    assert raft_node_1._log[1].term == 0
    assert raft_node_1._log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node_1._log[1].entry.value == 7
    assert raft_node_1._log[2].term == 1
    assert raft_node_1._log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node_1._log[2].entry.value == 11
    assert raft_node_1._log[3].term == 1
    assert raft_node_1._log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node_1._log[3].entry.value == 12
    assert raft_node_1._log[4].term == 1
    assert raft_node_1._log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node_1._log[4].entry.value == 13



async def _unittest_raft_leader_changes() -> None:
    # TODO: Test that log replication happens correctly if leadership changes
    pass
