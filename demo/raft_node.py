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

# DSDL files are automatically compiled by pycyphal import hook from sources pointed by CYPHAL_PATH env variable.
import sirius_cyber_corp  # This is our vendor-specific root namespace. Custom data types.
import pycyphal.application  # This module requires the root namespace "uavcan" to be transcompiled.

# Import other namespaces we're planning to use. Nested namespaces are not auto-imported, so in order to reach,
# say, "uavcan.node.Heartbeat", you have to "import uavcan.node".
import uavcan.node  # noqa
from raft_state import RaftState

_logger = logging.getLogger(__name__)

next_term_timeout = 0.5  # seconds


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

        # Raft-specific node variables
        # candidateId that received vote in current term (or null if none)
        self.voted_for: int | None = None
        # log entries; each entry contains command for state machine,
        # and term when entry was received by leader
        self.log: typing.List[typing.Tuple[str, int]] = []
        # state
        self.state: RaftState = RaftState.FOLLOWER
        self.last_print_time: float = 0.0

        self.current_term: int = 0
        self.current_term_timestamp: float = 0.0

        self.last_message_timestamp: float = 0.0
        # choose election timeout randomly between 150 and 300 ms
        self.election_timeout: float = 0.15 + 0.15 * os.urandom(1)[0] / 255.0
        self.next_election_timeout: float = (
            self.last_message_timestamp + self.election_timeout
        )

        # The Node class is basically the central part of the library -- it is the bridge between the application and
        # the UAVCAN network. Also, it implements certain standard application-layer functions, such as publishing
        # heartbeats and port introspection messages, responding to GetInfo, serving the register API, etc.
        # The register file stores the configuration parameters of our node (you can inspect it using SQLite Browser).
        self._node = pycyphal.application.make_node(node_info, RaftNode.REGISTER_FILE)

        # Published heartbeat fields can be configured as follows.
        self._node.heartbeat_publisher.mode = uavcan.node.Mode_1.OPERATIONAL  # type: ignore
        self._node.heartbeat_publisher.vendor_specific_status_code = os.getpid() % 100

        # Create an RPC-server. (RequestVote)
        try:
            _logger.info("Request vote service is enabled")
            srv_request_vote = self._node.get_server(
                sirius_cyber_corp.RequestVote_1, "request_vote"
            )
            srv_request_vote.serve_in_background(self._serve_request_vote)
        except pycyphal.application.register.MissingRegisterError:
            _logger.info(
                "The request vote service is disabled by configuration (UAVCAN__SRV__REQUEST_VOTE__ID missing)"
            )

        # Create an RPC-server. (AppendEntries)
        # try:
        #     srv_append_entries = self._node.get_server(
        #         sirius_cyber_corp.AppendEntries_1, "append_entries"
        #     )
        #     srv_append_entries.serve_in_background(self._serve_append_entries)
        # except pycyphal.application.register.MissingRegisterError: # UAVCAN__SRV__APPEND_ENTRIES__ID
        #     logging.info("The append entries service is disabled by configuration (UAVCAN__SRV__APPEND_ENTRIES__ID missing)")

        # Create another RPC-server using a standard service type for which a fixed service-ID is defined.
        # We don't specify the port name so the service-ID defaults to the fixed port-ID.
        # We could, of course, use it with a different service-ID as well, if needed.
        self._node.get_server(uavcan.node.ExecuteCommand_1).serve_in_background(
            self._serve_execute_command
        )

        self._node.start()  # Don't forget to start the node!

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
        self.election_timeout = 0.15 + 0.15 * os.urandom(1)[0] / 255.0
        self.next_election_timeout = self.last_message_timestamp + self.election_timeout
        # Send RequestVote RPCs to all other servers
        # If votes received from majority of servers: become leader
        # If AppendEntries RPC received from new leader: convert to follower
        # If election timeout elapses: start new election

    @staticmethod
    async def _serve_append_entries(
        request: sirius_cyber_corp.AppendEntries_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> sirius_cyber_corp.AppendEntries_1.Response:
        # TODO: implement this
        _logger.info(
            "Append entries request %s from node %d", request, metadata.client_node_id
        )
        return sirius_cyber_corp.AppendEntries_1.Response(
            term=1,
            success=True,
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
            await asyncio.sleep(0.1)
            if time.time() - self.last_print_time > 2:
                self.last_print_time = time.time()
                # print node id and the state
                _logger.info(
                    "Node ID: %d -- Current state: %s", self._node.id, self.state
                )
            # if term timeout is reached, increase term
            if time.time() - self.current_term_timestamp > next_term_timeout:
                self.current_term_timestamp = time.time()
                self.current_term += 1
                # self.voted_for = None
                # self.state = RaftState.FOLLOWER
                _logger.info(
                    "Node ID: %d -- Term timeout reached, increasing term to %d",
                    self._node.id,
                    self.current_term,
                )
            # if election timeout is reached, convert to candidate and start election
            if time.time() - self.last_message_timestamp > self.next_election_timeout:
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
    os.environ["UAVCAN__NODE__ID"] = "42"
    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    raft_node = RaftNode()
    assert raft_node._node.id == 42


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
    #         and the candidate's term is greater than or equal to the node's term
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


async def _unittest_raft_fsm() -> None:
    """ "
    Test the Raft FSM

    - at any given time each server is in one of three states: leader, follower or candidate
    - state transitions:
        - follower
            - start as a follower
            - if election timeout, convert to candidate and start election
        - candidate
            - if votes received from majority of servers: become leader
            - if election timeout elapses: start new election
            - discovers current leader or new term: convert to follower
        - leader
            - discovers current leader with higher term: convert to follower
    """
    # start new raft node
    raft_node_1 = RaftNode()
    # test if it is a follower
    assert raft_node_1.state == RaftState.FOLLOWER

    # let run until election timeout
    election_timeout = raft_node_1.election_timeout
    asyncio.create_task(raft_node_1.run())
    await asyncio.sleep(election_timeout + 0.1)
    # test if it is a candidate
    assert raft_node_1.state == RaftState.CANDIDATE

    # let run until election timeout
    await asyncio.sleep(election_timeout + 0.1)
    # test if it is a candidate still
    assert raft_node_1.state == RaftState.CANDIDATE

    # make additional raft nodes
    # raft_node_2 = RaftNode()
    # raft_node_3 = RaftNode()

    # assert raft_node_1.state == RaftState.LEADER
    # assert raft_node_2.state == RaftState.FOLLOWER
    # assert raft_node_3.state == RaftState.FOLLOWER

    # test if it is a leader

    # send message with higher term
    # test if it is a follower

    # let run until election timeout
    # test if it is a candidate

    raft_node_1.close()


def _unittest_raft_node_append_entries_rpc() -> None:
    assert 1 == 1
