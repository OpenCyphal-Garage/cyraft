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

EMPTY_ENTRIES = sirius_cyber_corp.Entry_1(
    topicName=uavcan.primitive.String_1(value=""),  # empty topic name
    topicId=0,  # empty topic id
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
        # candidateId that received vote in current term (or null if none)
        self.voted_for: int | None = None
        # log entries; each entry contains command for state machine,
        # and term when entry was received by leader
        self.log: typing.List[typing.Tuple[str, int]] = []
        # state
        self.prev_state: RaftState = RaftState.FOLLOWER  # for testing purposes
        self.state: RaftState = RaftState.FOLLOWER
        self.current_term: int = 0
        self.current_term_timestamp: float = time.time()
        self.last_message_timestamp: float = time.time()
        # choose election timeout randomly between 150 and 300 ms
        self.election_timeout: float = 0.15 + 0.15 * os.urandom(1)[0] / 255.0
        self.cluster: typing.List[RaftNode] = []

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
                term=self.current_term, voteGranted=False
            )

        # If voted_for is null or candidateId, and candidate’s log is at
        # least as up-to-date as receiver’s log, grant vote (§5.2, §5.4) # TODO: implement log comparison
        elif self.voted_for is None or self.voted_for == request.candidateID:
            _logger.info("Request vote request granted")
            _logger.info("self.voted_for: %s", self.voted_for)
            _logger.info("request.candidateID: %d", request.candidateID)
            self.voted_for = request.candidateID
            return sirius_cyber_corp.RequestVote_1.Response(
                term=self.current_term,
                voteGranted=True,
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
            candidateID=self._node.id,
            lastLogIndex=0,  # TODO: implement log
            lastLogTerm=0,  # TODO: implement log
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
                if response.voteGranted:
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
            and request.topicLog.entries == EMPTY_ENTRIES
        ):
            _logger.info("Heartbeat received")
            self.last_message_timestamp = metadata.timestamp
            return sirius_cyber_corp.AppendEntries_1.Response(
                term=self.current_term, success=True
            )

        # Reply false if term < currentTerm (§5.1)
        # if request.term < self.current_term:
        #     _logger.info("Append entries request denied")
        #     _logger.info("request.term: %d", request.term)
        #     _logger.info("self.current_term: %d", self.current_term)
        #     return sirius_cyber_corp.AppendEntries_1.Response(
        #         term=self.current_term, success=False
        #     )

        # Reply false if log doesn’t contain an entry at prevLogIndex
        # whose term matches prevLogTerm (§5.3)
        # TODO: implement log

        # If an existing entry conflicts with a new one (same index
        # but different terms), delete the existing entry and all that
        # follow it (§5.3)

        # Append any new entries not already in the log

        # If leaderCommit > commitIndex, set commitIndex =
        # min(leaderCommit, index of last new entry)
        _logger.error("Should not reach here!")
        _logger.error("request.term: %d", request.term)
        _logger.error("self.current_term: %d", self.current_term)
        _logger.error("entries: %s", request.entries)
        return sirius_cyber_corp.AppendEntries_1.Response(
            term=1,
            success=False,
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


async def _unittest_raft_node_election_timeout_heartbeat() -> None:
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    raft_node.voted_for = 42
    raft_node.set_election_timeout(ELECTION_TIMEOUT)
    asyncio.create_task(raft_node.run())
    await asyncio.sleep(
        ELECTION_TIMEOUT * 0.90
    )  # wait for right before election timeout
    # calculate number of terms passed
    terms_passed = raft_node.current_term
    # send heartbeat
    empty_topic_log = sirius_cyber_corp.LogEntry_1(
        term=raft_node.current_term,  # leader's term is equal to follower's term
        entries=EMPTY_ENTRIES,  # empty log entries
    )
    message_timestamp = time.time()
    await raft_node._serve_append_entries(
        sirius_cyber_corp.AppendEntries_1.Request(
            term=terms_passed,  # leader's term
            leaderID=42,  # so follower can redirect clients
            prevLogIndex=0,  # index of log entry immediately preceding new ones
            prevLogTerm=0,  # term of prevLogIndex entry
            topicLog=empty_topic_log,  # log entries to store (empty for heartbeat)
            leaderCommit=0,  # leader's commitIndex
        ),
        pycyphal.presentation.ServiceRequestMetadata(
            client_node_id=42,  # leader's node id
            timestamp=message_timestamp,  # leader's timestamp
            priority=0,  # leader's priority
            transfer_id=0,  # leader's transfer id
        ),
    )
    # wait for heartbeat to be processed [election is reached but shouldn't become leader due to hearbeat
    await asyncio.sleep(ELECTION_TIMEOUT * 0.1)
    assert raft_node.state == RaftState.FOLLOWER
    assert raft_node.voted_for == 42
    # assert last_message_timestamp has been updated
    assert raft_node.last_message_timestamp == message_timestamp


async def _unittest_raft_node_request_vote_rpc() -> None:
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()

    request = sirius_cyber_corp.RequestVote_1.Request(
        term=1,  # candidate's term
        candidateID=42,  # candidate requesting vote
        lastLogIndex=1,  # index of candidate's last log entry
        lastLogTerm=1,  # term of candidate's last log entry
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
    await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == 43
    # assert response.voteGranted == False # TODO: how to retrieve response?

    # test 2: vote not granted if node's term is greater than candidate's term
    raft_node.voted_for = None  # node has not voted for any candidate
    raft_node.current_term = 2  # node's term is greater than candidate's term
    assert request.term < raft_node.current_term
    await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == None
    # assert response.voteGranted == False # TODO: how to test this?

    # test 3: vote granted if not voted for another candidate
    #         and the candidate's term is greater than the node's term
    raft_node.voted_for = None
    raft_node.current_term = 0
    assert not request.term < raft_node.current_term
    await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == 42
    # assert response.voteGranted == True # TODO: how to retrieve response?

    # test 4: vote granted if not voted for another candidate
    #         and the candidate's term is equal to the node's term
    raft_node.voted_for = None  # node has not voted for any candidate
    raft_node.current_term = 1  # node's term is equal to candidate's term
    assert not request.term < raft_node.current_term
    await raft_node._serve_request_vote(request, metadata)
    assert raft_node.voted_for == 42
    # assert response.voteGranted == True # TODO: how to retrieve response?
