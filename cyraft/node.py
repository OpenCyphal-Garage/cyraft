#!/usr/bin/env python3
# Distributed under CC0 1.0 Universal (CC0 1.0) Public Domain Dedication.
# pylint: disable=ungrouped-imports,wrong-import-position

import os
import sys
import pathlib
import asyncio
import logging
import pycyphal
import typing
import time
import numpy as np

# DSDL files are automatically compiled by pycyphal import hook from sources pointed by CYPHAL_PATH env variable.
import sirius_cyber_corp  # This is our vendor-specific root namespace. Custom data types.
import pycyphal.application  # This module requires the root namespace "uavcan" to be transcompiled.

# Import other namespaces we're planning to use. Nested namespaces are not auto-imported, so in order to reach,
# say, "uavcan.node.Heartbeat", you have to "import uavcan.node".
import uavcan.node  # noqa
from state import RaftState

_logger = logging.getLogger(__name__)

TERM_TIMEOUT = 0.5  # seconds
ELECTION_TIMEOUT = 5  # seconds

EMPTY_ENTRY = sirius_cyber_corp.Entry_1(
    name=uavcan.primitive.String_1(value=""),  # empty topic name
    value=0,  # empty topic id
)


class RaftNode:
    REGISTER_FILE = "raft_node.db"
    """
    The register file stores configuration parameters of the local application/node. The registers can be modified
    at launch via environment variables and at runtime via RPC-service "uavcan.register.Access".
    The file will be created automatically if it doesn't exist.
    """

    def __init__(self) -> None:
        node_info = uavcan.node.GetInfo_1.Response(
            software_version=uavcan.node.Version_1(major=1, minor=0),
            name="org.opencyphal.pycyphal.demo.demo_node",
        )

        ########################################
        ##### Raft-specific node variables #####
        ########################################
        self.closing = False

        self.prev_state: RaftState = RaftState.FOLLOWER  # for testing purposes
        self.state: RaftState = RaftState.FOLLOWER

        self.current_term_timestamp: float = time.time()
        self.last_message_timestamp: float = time.time()
        self.election_timeout: float = (
            0.15 + 0.15 * os.urandom(1)[0] / 255.0
        )  # random between 150 and 300 ms

        self.cluster: typing.List[RaftNode] = []

        ## Persistent state on all servers
        self.current_term: int = 0
        self.voted_for: int | None = None
        self.log: typing.List[sirius_cyber_corp.LogEntry_1] = []
        # index 0 of log contains own node info # TODO: fill out properly
        self.log.append(
            sirius_cyber_corp.LogEntry_1(
                term=0,
                entry=sirius_cyber_corp.Entry_1(
                    name=uavcan.primitive.String_1(value=""),
                    value=0,
                ),
            )
        )

        ## Volatile state on all servers
        self.commit_index: int = 0
        self.last_applied: int = 0

        ## Volatile state on leaders
        self.next_index: typing.List[int] = []
        self.match_index: typing.List[int] = []

        ########################################
        #####       UAVCAN-specific        #####
        ########################################
        self._node = pycyphal.application.make_node(node_info, RaftNode.REGISTER_FILE)

        self._node.heartbeat_publisher.mode = uavcan.node.Mode_1.OPERATIONAL  # type: ignore
        self._node.heartbeat_publisher.vendor_specific_status_code = os.getpid() % 100

        # Create an RPC-server. (RequestVote)
        try:
            srv_request_vote = self._node.get_server(
                sirius_cyber_corp.RequestVote_1, "request_vote"
            )
            srv_request_vote.serve_in_background(self._serve_request_vote)
            _logger.info("Request vote service is enabled")
        except pycyphal.application.register.MissingRegisterError:
            _logger.info(
                "The request vote service is disabled by configuration (UAVCAN__SRV__REQUEST_VOTE__ID missing)"
            )

        # Create an RPC-server. (AppendEntries)
        try:
            srv_append_entries = self._node.get_server(
                sirius_cyber_corp.AppendEntries_1, "append_entries"
            )
            srv_append_entries.serve_in_background(self._serve_append_entries)
            _logger.info("Append entries service is enabled")
        except pycyphal.application.register.MissingRegisterError:
            logging.info(
                "The append entries service is disabled by configuration (UAVCAN__SRV__APPEND_ENTRIES__ID missing)"
            )

        self._node.get_server(uavcan.node.ExecuteCommand_1).serve_in_background(
            self._serve_execute_command
        )

        self._node.start()  # Don't forget to start the node!

    def set_election_timeout(self, timeout: float) -> None:
        self.election_timeout = timeout

    def add_remote_node(self, node_id) -> None:
        if node_id not in self.cluster:
            self.cluster.append(node_id)

    async def _serve_request_vote(
        self,
        request: sirius_cyber_corp.RequestVote_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> sirius_cyber_corp.RequestVote_1.Response:
        _logger.info(
            "\033[94m Request vote request %s from node %d \033[0m",
            request,
            metadata.client_node_id,
        )
        # QUESTION: metadata is not used?
        # Reply false if term < self.current_term (§5.1)
        if request.term < self.current_term or self.voted_for is not None:
            _logger.info("Request vote request denied")
            _logger.info("request.term: %d", request.term)
            _logger.info("self.current_term: %d", self.current_term)
            _logger.info("self.voted_for: %s", self.voted_for)
            return sirius_cyber_corp.RequestVote_1.Response(
                term=self.current_term, vote_granted=False
            )

        # If voted_for is null or candidateId, and candidate’s log is at
        # least as up-to-date as receiver’s log, grant vote (§5.2, §5.4) # TODO: implement log comparison
        elif self.voted_for is None or self.voted_for == metadata.client_node_id:
            _logger.info("Request vote request granted")
            _logger.info("self.voted_for: %s", self.voted_for)
            _logger.info("request.candidateID: %d", metadata.client_node_id)
            self.voted_for = metadata.client_node_id
            return sirius_cyber_corp.RequestVote_1.Response(
                term=self.current_term,
                vote_granted=True,
            )

        _logger.error("Should not reach here!")
        _logger.error("request.term: %d", request.term)
        _logger.error("self.current_term: %d", self.current_term)
        _logger.error("self.voted_for: %d", self.voted_for)

    async def _start_election(self) -> None:
        _logger.info("Node ID: %d -- Starting election", self._node.id)
        # Increment currentTerm
        self.current_term += 1
        # Vote for self
        self.voted_for = self._node.id
        # Reset election timeout
        self.last_message_timestamp = time.time()
        # Send RequestVote RPCs to all other servers
        request = sirius_cyber_corp.RequestVote_1.Request(
            term=self.current_term,
            candidate_id=self._node.id,
            last_log_index=0,  # TODO: implement log
            last_log_term=0,  # TODO: implement log
        )
        metadata = pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=self._node.id,
            timestamp=time.time(),
            priority=1,
            transfer_id=0,
        )
        # Send request vote to all nodes in cluster, count votes
        number_of_nodes = len(self.cluster)
        number_of_votes = 1  # Vote for self
        for remote_node in self.cluster:
            if remote_node._node.id != self._node.id:
                _logger.info("Sending request vote to node %d", remote_node._node.id)
                response = await remote_node._serve_request_vote(request, metadata)
                _logger.info(
                    "Response from node %d: %s", remote_node._node.id, response
                )
                if response.vote_granted:
                    number_of_votes += 1
        # If votes received from majority of servers: become leader
        if number_of_votes > number_of_nodes / 2:
            _logger.info("Node ID: %d -- Became leader", self._node.id)
            self.prev_state = self.state
            self.state = RaftState.LEADER
        else:
            _logger.info("Node ID: %d -- Election failed", self._node.id)
            # If AppendEntries RPC received from new leader: convert to follower
            # TODO: implement this

    async def _serve_append_entries(
        self,
        request: sirius_cyber_corp.AppendEntries_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> sirius_cyber_corp.AppendEntries_1.Response:
        _logger.info(
            "\033[94m Append entries request %s from node %d \033[0m",
            request,
            metadata.client_node_id,
        )

        # heartbeat processing
        if (
            request.term == self.current_term
            and request.log_entry.entry == EMPTY_ENTRY
            and metadata.client_node_id == self.voted_for  # heartbeat from leader
        ):
            _logger.info("Heartbeat received")
            self.last_message_timestamp = metadata.timestamp
            return sirius_cyber_corp.AppendEntries_1.Response(
                term=self.current_term, success=True
            )

        # Reply false if term < currentTerm (§5.1)
        if request.term < self.current_term:
            _logger.info("Append entries request denied (term < currentTerm)")
            _logger.info("request.term: %d", request.term)
            _logger.info("self.current_term: %d", self.current_term)
            return sirius_cyber_corp.AppendEntries_1.Response(
                term=self.current_term, success=False
            )

        # Reply false if log doesn’t contain an entry at prevLogIndex
        # whose term matches prevLogTerm (§5.3)
        try:
            if self.log[request.prev_log_index].term != request.prev_log_term:
                _logger.info("Append entries request denied (log mismatch)")
                return sirius_cyber_corp.AppendEntries_1.Response(
                    term=self.current_term, success=False
                )
        except IndexError:
            _logger.info("Append entries request denied (log mismatch 2)")
            return sirius_cyber_corp.AppendEntries_1.Response(
                term=self.current_term, success=False
            )

        # If an existing entry conflicts with a new one (same index
        # but different terms), delete the existing entry and all that
        # follow it (§5.3)
        index = request.prev_log_index + 1  # QUESTION: Is this correct?
        try:
            log_entry = self.log[index]
            if log_entry.term != request.log_entry.term:
                del self.log[index:]
                _logger.info("Deleted entries after index %d", index)
                _logger.info("log_entry.term: %d", log_entry.term)
                _logger.info("request.log_entry.term: %d", request.log_entry.term)
        except IndexError:
            pass

        # Append any new entries not already in the log
        # [in our implementation only a single entry is sent]
        self.log[index] = request.log_entry

        # If leaderCommit > commitIndex, set commitIndex =
        # min(leaderCommit, index of last new entry)
        if request.leader_commit > self.commit_index:
            self.commit_index = min(request.leader_commit, index)

        return sirius_cyber_corp.AppendEntries_1.Response(
            term=self.current_term, success=True
        )

    @staticmethod
    async def _serve_execute_command(
        request: uavcan.node.ExecuteCommand_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> uavcan.node.ExecuteCommand_1.Response:
        _logger.info(
            "Execute command request %s from node %d", request, metadata.client_node_id
        )
        if (
            request.command
            == uavcan.node.ExecuteCommand_1.Request.COMMAND_FACTORY_RESET
        ):
            try:
                os.unlink(
                    RaftNode.REGISTER_FILE
                )  # Reset to defaults by removing the register file.
            except OSError:  # Do nothing if already removed.
                pass
            return uavcan.node.ExecuteCommand_1.Response(
                uavcan.node.ExecuteCommand_1.Response.STATUS_SUCCESS
            )
        return uavcan.node.ExecuteCommand_1.Response(
            uavcan.node.ExecuteCommand_1.Response.STATUS_BAD_COMMAND
        )

    async def run(self) -> None:
        """
        The main method that runs the business logic. It is also possible to use the library in an IoC-style
        by using receive_in_background() for all subscriptions if desired.
        """
        _logger.info("Application Node started!")
        _logger.info("Running. Press Ctrl+C to stop.")

        while True:
            await asyncio.sleep(0.01)
            # if closing, break # TODO: this is not working? (see _unittest_raft_node_election_timeout)
            if self.closing:
                break
            # if term timeout is reached, increase term
            if time.time() - self.current_term_timestamp > TERM_TIMEOUT:
                self.current_term_timestamp = time.time()
                self.current_term += 1
                _logger.info(
                    "Node ID: %d -- Term timeout reached, increasing term to %d",
                    self._node.id,
                    self.current_term,
                )

            # if leader, send heartbeat to all nodes in cluster (before election timeout)
            if (
                self.state == RaftState.LEADER
                and time.time() - self.last_message_timestamp
                > self.election_timeout * 0.9
            ):
                await self._send_heartbeat()
            # if election timeout is reached, convert to candidate and start election
            if time.time() - self.last_message_timestamp > self.election_timeout:
                self.state = RaftState.CANDIDATE
                _logger.info(
                    "Node ID: %d -- Election timeout reached",
                    self._node.id,
                )
                await self._start_election()

    def close(self) -> None:
        """
        This will close all the underlying resources down to the transport interface and all publishers/servers/etc.
        All pending tasks such as serve_in_background()/receive_in_background() will notice this and exit automatically.
        """
        self.closing = True
        self._node.close()


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


async def _unittest_raft_node_init() -> None:
    """
    Test that the node is initialized correctly
    """
    os.environ["UAVCAN__NODE__ID"] = "42"
    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"
    raft_node = RaftNode()
    assert raft_node._node.id == 42
    assert raft_node.state == RaftState.FOLLOWER
    # Persistent states
    assert raft_node.current_term == 0
    assert raft_node.voted_for == None
    assert len(raft_node.log) == 1
    assert raft_node.log[0].term == 0
    # Volatile states
    assert raft_node.commit_index == 0
    assert raft_node.last_applied == 0


async def _unittest_raft_node_term_timeout() -> None:
    """
    Test that the node term is increased after the term timeout
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    raft_node.set_election_timeout(TERM_TIMEOUT * 5)
    asyncio.create_task(raft_node.run())
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node.current_term == 1
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node.current_term == 2
    await asyncio.sleep(TERM_TIMEOUT)
    assert raft_node.current_term == 3


async def _unittest_raft_node_election_timeout() -> None:
    """
    Test that the node converts to candidate after the election timeout

    Test that the node doesn't convert to candidate if it receives a hearbeat message
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    raft_node.set_election_timeout(TERM_TIMEOUT * 5)
    asyncio.create_task(raft_node.run())
    await asyncio.sleep(TERM_TIMEOUT * 5)
    assert raft_node.prev_state == RaftState.CANDIDATE
    assert raft_node.state == RaftState.LEADER  # TODO: fix this
    raft_node.close()
    # cancel all tasks # TODO: figure out how to close task properly
    # pending_tasks = asyncio.all_tasks()
    # for task in pending_tasks:
    #     task.cancel()
    # await asyncio.gather(*pending_tasks, return_exceptions=True)


async def _unittest_raft_node_election_timeout_heartbeat() -> None:
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    raft_node.voted_for = 42
    raft_node.set_election_timeout(ELECTION_TIMEOUT)

    asyncio.create_task(raft_node.run())
    await asyncio.sleep(
        ELECTION_TIMEOUT * 0.90
    )  # wait for right before election timeout

    # send heartbeat
    terms_passed = raft_node.current_term
    empty_topic_log = sirius_cyber_corp.LogEntry_1(
        term=raft_node.current_term,  # leader's term is equal to follower's term
        entry=EMPTY_ENTRY,  # empty log entries
    )
    message_timestamp = time.time()
    await raft_node._serve_append_entries(
        sirius_cyber_corp.AppendEntries_1.Request(
            term=terms_passed,  # leader's term
            prev_log_index=0,  # index of log entry immediately preceding new ones
            prev_log_term=0,  # term of prevLogIndex entry
            leader_commit=0,  # leader's commitIndex
            log_entry=empty_topic_log,  # log entries to store (empty for heartbeat)
        ),
        pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,  # leader's node id
            timestamp=message_timestamp,  # leader's timestamp
            priority=0,  # leader's priority
            transfer_id=0,  # leader's transfer id
        ),
    )

    # wait for heartbeat to be processed [election is reached but shouldn't become leader due to hearbeat]
    await asyncio.sleep(ELECTION_TIMEOUT * 0.1)
    assert raft_node.state == RaftState.FOLLOWER
    assert raft_node.voted_for == 42
    # last_message_timestamp should be updated
    assert raft_node.last_message_timestamp == message_timestamp

    ## test that the node converts to candidate after the election timeout [if no heartbeat is received]
    await asyncio.sleep(ELECTION_TIMEOUT)
    assert raft_node.prev_state == RaftState.CANDIDATE


async def _unittest_raft_node_request_vote_rpc() -> None:
    """
    - Reply false if term < currentTerm
    - If votedFor is null or candidateId, and candidate's log is at least as up-to-date as receiver's log, grant vote
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()

    request = sirius_cyber_corp.RequestVote_1.Request(
        term=1,  # candidate's term
        last_log_index=1,  # index of candidate's last log entry
        last_log_term=1,  # term of candidate's last log entry
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,  # voter's node id
        timestamp=0,  # voter's timestamp
        priority=0,  # voter's priority
        transfer_id=0,  # voter's transfer id
    )

    # test 1: vote not granted if already voted for another candidate
    raft_node.voted_for = 43  # node voted for another candidate
    raft_node.current_term = 1  # node's term is equal to candidate's term
    assert request.term == raft_node.current_term
    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == 43
    assert response.vote_granted == False

    # test 2: vote not granted if node's term is greater than candidate's term
    raft_node.voted_for = None  # node has not voted for any candidate
    raft_node.current_term = 2  # node's term is greater than candidate's term
    assert request.term < raft_node.current_term
    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == None
    assert response.vote_granted == False

    # test 3: vote granted if not voted for another candidate
    #         and the candidate's term is greater than the node's term
    raft_node.voted_for = None
    raft_node.current_term = 0
    assert not request.term < raft_node.current_term
    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == 42
    assert response.vote_granted == True

    # test 4: vote granted if not voted for another candidate
    #         and the candidate's term is equal to the node's term
    raft_node.voted_for = None  # node has not voted for any candidate
    raft_node.current_term = 1  # node's term is equal to candidate's term
    assert not request.term < raft_node.current_term
    response = await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == 42
    assert response.vote_granted == True


async def _unittest_raft_node_append_entries_rpc() -> None:
    """
    - Reply false if term < currentTerm
    - Reply false if log doesn't contain an entry at prevLogIndex whose term matches prevLogTerm
    - If an existing entry conflicts with a new one (same index but different terms),
      delete the existing entry and all that follow it
    - Append any new entries not already in the log
    - If leaderCommit > commitIndex, set commitIndex = min(leaderCommit, index of last new entry)
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()

    # test 1: reply false if term < currentTerm
    raft_node.current_term = 1  # node's term
    log_entry = sirius_cyber_corp.LogEntry_1(
        term=0,  # leader's term (lower than node's term)
        entry=sirius_cyber_corp.Entry_1(
            topic=uavcan.primitive.String_1(value="raft/test"),
            topic_id=1,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=0,  # leader's term
        leader_id=42,
        prev_log_index=0,
        prev_log_term=0,
        log_entry=log_entry,
    )
    metadata = pycyphal.presentation.ServiceRequestMetadata(
        client_node_id=42,
        timestamp=time.time(),
        priority=0,
        transfer_id=0,
    )
    assert request.term < raft_node.current_term
    response = await raft_node._serve_append_entries(request, metadata)
    assert response.term == raft_node.current_term
    assert response.success == False
    assert raft_node.log == []

    # test 2: reply false if log doesn't contain an entry at prevLogIndex whose term matches prevLogTerm
    #   case 1: Log mismatch
    node_prev_log_term = 1
    raft_node.log = [
        sirius_cyber_corp.LogEntry_1(
            term=node_prev_log_term,
            entry=sirius_cyber_corp.Entry_1(
                topic=uavcan.primitive.String_1(value="raft/test"),
                topic_id=1,
            ),
        ),
    ]
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=1,  # leader's term
        leader_id=42,
        prev_log_index=0,
        prev_log_term=0,  # leader's term
        log_entry=log_entry,
    )
    assert request.prev_log_term != node_prev_log_term
    response = await raft_node._serve_append_entries(request, metadata)
    assert response.term == raft_node.current_term
    assert response.success == False

    #   case 2: IndexError
    raft_node.log = []
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=1,
        leader_id=42,
        prev_log_index=0,
        prev_log_term=0,
        log_entry=log_entry,
    )
    assert request.prev_log_term != node_prev_log_term
    response = await raft_node._serve_append_entries(request, metadata)
    assert response.term == raft_node.current_term
    assert response.success == False

    # test 3: if an existing entry conflicts with a new one (same index but different terms),
    #         delete the existing entry and all that follow it
    log_entry_1 = sirius_cyber_corp.LogEntry_1(
        term=1,
        entry=sirius_cyber_corp.Entry_1(
            topic=uavcan.primitive.String_1(value="raft/test_1"),
            topic_id=1,
        ),
    )
    log_entry_2 = sirius_cyber_corp.LogEntry_1(
        term=2,
        entry=sirius_cyber_corp.Entry_1(
            topic=uavcan.primitive.String_1(value="raft/test_2"),
            topic_id=2,
        ),
    )
    log_entry_3 = sirius_cyber_corp.LogEntry_1(
        term=3,
        entry=sirius_cyber_corp.Entry_1(
            topic=uavcan.primitive.String_1(value="raft/test_3"),
            topic_id=3,
        ),
    )
    raft_node.log = [
        log_entry_1,
        log_entry_2,
        log_entry_3,
    ]

    log_entry = sirius_cyber_corp.LogEntry_1(
        term=3,  # (higher than log_entry_2's term)
        entry=sirius_cyber_corp.Entry_1(
            topic=uavcan.primitive.String_1(value="raft/test_4"),
            topic_id=2,
        ),
    )
    request = sirius_cyber_corp.AppendEntries_1.Request(
        term=3,
        leader_id=42,
        prev_log_index=0,
        prev_log_term=1,
        log_entry=log_entry,
        leader_commit=4,
    )

    response = await raft_node._serve_append_entries(request, metadata)
    assert response.term == raft_node.current_term
    assert response.success == True

    assert False

    # test 4: append any new entries not already in the log

    # test 5: if leaderCommit > commitIndex, set commitIndex = min(leaderCommit, index of last new entry)
