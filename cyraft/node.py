#!/usr/bin/env python3
# Distributed under CC0 1.0 Universal (CC0 1.0) Public Domain Dedication.
# pylint: disable=ungrouped-imports,wrong-import-position

import os
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
from .state import RaftState

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)

# ANSI colors for logging
c = {
    "end_color": "\033[0m",
    "raft_logic": "\033[36m",  # CYAN
    "request_vote": "\033[35m",  # PURPLE/"MAGENTA"
    "append_entries": "\033[33m",  # YELLOW
}

TERM_TIMEOUT = 0.5  # seconds
ELECTION_TIMEOUT = 5  # seconds


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
        self._prev_state: RaftState = RaftState.FOLLOWER  # for testing purposes
        self._state: RaftState = RaftState.FOLLOWER

        self._election_timer: asyncio.TimerHandle
        self._next_election_timeout: float
        self._term_timer: asyncio.TimerHandle
        self._next_term_timeout: float

        self._election_timeout: float = 0.15 + 0.15 * os.urandom(1)[0] / 255.0  # random between 150 and 300 ms
        self._term_timeout = TERM_TIMEOUT
        # assert (
        #     self._term_timeout < self._election_timeout / 2
        # ), "Term timeout must be less than half of election timeout"

        self._cluster: typing.List[int] = []
        self._request_vote_clients: typing.List[pycyphal.application.Client] = []
        self._append_entries_clients: typing.List[pycyphal.application.Client] = []

        ## Persistent state on all servers
        self._term: int = 0
        self._voted_for: int | None = None
        self._log: typing.List[sirius_cyber_corp.LogEntry_1] = []
        # index 0 contains an empty entry (so that the first entry starts at index 1 as per Raft paper)
        self._log.append(
            sirius_cyber_corp.LogEntry_1(
                term=0,
                entry=None,
            )
        )

        ## Volatile state on all servers
        self._commit_index: int = 0
        # self.last_applied: int = 0 # QUESTION: Is this even necessary? Do we have a "state machine"?

        ## Volatile state on leaders
        self._next_index: typing.List[int] = []
        self._match_index: typing.List[int] = []

        ########################################
        #####       UAVCAN-specific        #####
        ########################################
        self._node = pycyphal.application.make_node(node_info, RaftNode.REGISTER_FILE)

        self._node.heartbeat_publisher.mode = uavcan.node.Mode_1.OPERATIONAL  # type: ignore
        self._node.heartbeat_publisher.vendor_specific_status_code = os.getpid() % 100

        # Create an RPC-server. (RequestVote)
        self._node.get_server(sirius_cyber_corp.RequestVote_1, "request_vote").serve_in_background(
            self._serve_request_vote
        )

        # Create an RPC-server. (AppendEntries)
        self._node.get_server(sirius_cyber_corp.AppendEntries_1, "append_entries").serve_in_background(
            self._serve_append_entries
        )

        # Create an RPC-server. (ExecuteCommand)
        self._node.get_server(uavcan.node.ExecuteCommand_1).serve_in_background(self._serve_execute_command)

        self._node.start()

    @property
    def election_timeout(self) -> float:
        return self._election_timeout

    @election_timeout.setter
    def election_timeout(self, timeout: float) -> None:
        if timeout > 0:
            self._election_timeout = timeout
        else:
            raise ValueError("Election timeout must be greater than 0")

    @property
    def term_timeout(self) -> float:
        return self._term_timeout

    @term_timeout.setter
    def term_timeout(self, timeout: float) -> None:
        if timeout > 0:
            self._term_timeout = timeout
        else:
            raise ValueError("Term timeout must be greater than 0")

    def add_remote_node(self, remote_node_id: int | list[int]) -> None:
        """
        This method adds a remote node to the cluster. It also creates the necessary clients for the remote node.
        """
        if isinstance(remote_node_id, int):
            remote_node_id = [remote_node_id]

        for node_id in remote_node_id:
            if node_id not in self._cluster and node_id != self._node.id:
                _logger.info(c["raft_logic"] + f"Adding node {node_id} to cluster" + c["end_color"])
                self._cluster.append(node_id)
                request_vote_client = self._node.make_client(sirius_cyber_corp.RequestVote_1, node_id, "request_vote")
                self._request_vote_clients.append(request_vote_client)
                append_entries_client = self._node.make_client(
                    sirius_cyber_corp.AppendEntries_1, node_id, "append_entries"
                )
                self._append_entries_clients.append(append_entries_client)
                self._next_index.append(1)
                self._match_index.append(0)

        total_nodes = len(self._cluster)
        assert len(self._request_vote_clients) == total_nodes
        assert len(self._append_entries_clients) == total_nodes
        assert len(self._next_index) == total_nodes
        assert len(self._match_index) == total_nodes

    def remove_remote_node(self, remote_node_id: int) -> None:
        """
        This method is used to remove a remote node from the cluster.
        It also removes the corresponding clients for the remote node.
        """
        if remote_node_id in self._cluster:
            _logger.info(c["raft_logic"] + f"Removing node {remote_node_id} from cluster" + c["end_color"])
            index = self._cluster.index(remote_node_id)
            self._cluster.pop(index)
            self._request_vote_clients.pop(index)
            self._append_entries_clients.pop(index)
            self._next_index.pop(index)
            self._match_index.pop(index)

    async def _serve_request_vote(
        self,
        request: sirius_cyber_corp.RequestVote_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> sirius_cyber_corp.RequestVote_1.Response:
        """
        This method receives the request vote request from the candidate and calls the implementation method.
        """
        _logger.info(
            c["request_vote"] + "Node ID: %d -- Request vote request %s from node %d" + c["end_color"],
            self._node.id,
            request,
            metadata.client_node_id,
        )
        response = await self._serve_request_vote_impl(request, metadata.client_node_id)
        return response

    async def _serve_request_vote_impl(
        self, request: sirius_cyber_corp.RequestVote_1.Request, client_node_id: int
    ) -> sirius_cyber_corp.RequestVote_1.Response:
        """
        This method is used to serve the request_vote RPC.
        Depending on:
            - the term of the request
            - the term of the node
            - the voted_for of the node
        the method will either grant or deny the vote.
        """
        # Reply false if term < self._term (§5.1)
        # (or if already voted for another candidate in this term)
        if request.term < self._term or (self._voted_for is not None and request.term == self._term):
            _logger.info(
                c["request_vote"]
                + "Request vote request denied (term < self._term or already voted for another candidate in this term)"
                + c["end_color"]
            )
            return sirius_cyber_corp.RequestVote_1.Response(term=self._term, vote_granted=False)

        # If voted_for is null or candidateId, and candidate’s log is at
        # least as up-to-date as receiver’s log, grant vote (§5.2, §5.4)
        elif self._voted_for is None or self._voted_for == client_node_id:
            # log comparison
            if self._log[request.last_log_index].term == request.last_log_term:
                _logger.info(c["request_vote"] + "Request vote request granted" + c["end_color"])
                self._voted_for = client_node_id
                self._term = request.term
                return sirius_cyber_corp.RequestVote_1.Response(
                    term=self._term,
                    vote_granted=True,
                )
            else:
                _logger.info(c["request_vote"] + "Request vote request denied (failed log comparison)" + c["end_color"])
                return sirius_cyber_corp.RequestVote_1.Response(term=self._term, vote_granted=False)

        # # If CANDIDATE and remote CANDIDATE's term is greater/equal than self._term, then step down and grant vote
        # elif self._state == RaftState.CANDIDATE and request.term >= self._term:
        #     # log comparison
        #     if self._log[request.last_log_index].term == request.last_log_term:
        #         _logger.info(c["request_vote"] + "Request vote request granted" + c["end_color"])
        #         self._state = RaftState.FOLLOWER
        #         self._voted_for = client_node_id
        #         self._term = request.term
        #         return sirius_cyber_corp.RequestVote_1.Response(
        #             term=self._term,
        #             vote_granted=True,
        #         )
        #     else:
        #         _logger.info(c["request_vote"] + "Request vote request denied (failed log comparison)" + c["end_color"])
        #         return sirius_cyber_corp.RequestVote_1.Response(term=self._term, vote_granted=False)

        _logger.info(
            c["request_vote"] + "Node ID: %d -- Request vote request denied (unknown reason)" + c["end_color"],
            self._node.id,
        )
        _logger.info(
            c["request_vote"] + "Node ID: %d -- Current term: %d, Request term: %d" + c["end_color"],
            self._node.id,
            self._term,
            request.term,
        )
        assert False, "Should not reach here!"

    async def _start_election(self) -> None:
        """
        This method is used to start an election.
        It will send request vote to all the other nodes in the cluster.
        If the node receives a majority of votes, it will become the leader.
        If not, it will reset the election timeout and become a follower.
        """
        assert self._state == RaftState.CANDIDATE, "Election can only be started by a candidate"

        _logger.info(c["raft_logic"] + "Node ID: %d -- Starting election" + c["end_color"], self._node.id)
        # Increment currentTerm
        self._term += 1
        # Vote for self
        self._voted_for = self._node.id
        # Send RequestVote RPCs to all other servers
        last_log_index = len(self._log) - 1  # if log is empty (only entry is at index zero), last_log_index = 0
        request = sirius_cyber_corp.RequestVote_1.Request(
            term=self._term,
            last_log_index=last_log_index,
            last_log_term=self._log[last_log_index].term,
        )
        # Send request vote to all nodes in cluster, count votes
        number_of_nodes = len(self._cluster) + 1  # +1 for self
        number_of_votes = 1  # Vote for self
        for remote_node_index, remote_client in enumerate(self._request_vote_clients):  # index allows to find node id
            remote_node_id = self._cluster[remote_node_index]
            _logger.info(
                c["raft_logic"] + "Node ID: %d -- Sending request vote to node %d" + c["end_color"],
                self._node.id,
                remote_node_id,
            )
            response = await remote_client(request)
            if response:
                _logger.info(
                    c["raft_logic"] + "Node ID: %d -- Response from node %d: %s" + c["end_color"],
                    self._node.id,
                    remote_node_id,
                    response,
                )
                if response.vote_granted:
                    number_of_votes += 1
            else:
                _logger.info(
                    c["raft_logic"] + "Node ID: %d -- No response from node %d" + c["end_color"],
                    self._node.id,
                    remote_node_id,
                )

        # If votes received from majority of servers: become leader
        if number_of_votes > number_of_nodes / 2:  # int(5/2) = 2, int(3/2) = 1
            _logger.info(c["raft_logic"] + "Node ID: %d -- Became leader" + c["end_color"], self._node.id)
            self._change_state(RaftState.LEADER)
        else:
            _logger.info(c["raft_logic"] + "Node ID: %d -- Election failed" + c["end_color"], self._node.id)
            # If election fails, revert to follower
            self._voted_for = None
            self._change_state(RaftState.FOLLOWER)

    async def _serve_append_entries(
        self,
        request: sirius_cyber_corp.AppendEntries_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> sirius_cyber_corp.AppendEntries_1.Response:
        """
        This method receives the append entries requests from the leader and processes them in one of the following ways:
        1) If the request is a heartbeat, it will reset the election timeout and return a success response.
        2) If the request is not a heartbeat, it will:
            a) Check if the term is less than the current term, if so it will return a failure response.
            b) Check if the log entry at the previous index matches the previous term, if not it will return a failure response.
        3) If the above checks have passed, it will call _append_entries_processing to append the new entry to the log.
        """
        _logger.info(
            c["append_entries"] + "Node ID: %d -- Append entries request %s from node %d" + c["end_color"],
            self._node.id,
            request,
            metadata.client_node_id,
        )

        # heartbeat processing
        if len(request.log_entry) == 0:  # empty means heartbeat
            if request.term < self._term:
                _logger.info(
                    c["append_entries"] + "Node ID: %d -- Heartbeat denied (term < currentTerm)" + c["end_color"],
                    self._node.id,
                )
                return sirius_cyber_corp.AppendEntries_1.Response(term=self._term, success=False)
            else:  # request.term >= self._term
                _logger.info(c["append_entries"] + "Node ID: %d -- Heartbeat received" + c["end_color"], self._node.id)
                if metadata.client_node_id != self._voted_for and request.term > self._term:
                    _logger.info(
                        c["append_entries"] + "Node ID: %d -- Heartbeat from new leader: %d" + c["end_color"],
                        self._node.id,
                        metadata.client_node_id,
                    )
                    self._voted_for = metadata.client_node_id
                self._change_state(RaftState.FOLLOWER)
                self._reset_election_timeout()
                self._term = request.term  # update term
                return sirius_cyber_corp.AppendEntries_1.Response(term=self._term, success=True)

        # Reply false if term < currentTerm (§5.1)
        if request.term < self._term:
            _logger.info(
                c["append_entries"]
                + "Node ID: %d -- Append entries request denied (term < currentTerm)"
                + c["end_color"],
                self._node.id,
            )
            return sirius_cyber_corp.AppendEntries_1.Response(term=self._term, success=False)

        # Reply false if log doesn’t contain an entry at prevLogIndex
        # whose term matches prevLogTerm (§5.3)
        try:
            if self._log[request.prev_log_index].term != request.prev_log_term:
                _logger.info(
                    c["append_entries"]
                    + "Node ID: %d -- Append entries request denied (log mismatch)"
                    + c["end_color"],
                    self._node.id,
                )
                return sirius_cyber_corp.AppendEntries_1.Response(term=self._term, success=False)
        except IndexError:
            _logger.info(
                c["append_entries"] + "Node ID: %d -- Append entries request denied (log mismatch 2)" + c["end_color"],
                self._node.id,
            )
            return sirius_cyber_corp.AppendEntries_1.Response(term=self._term, success=False)

        self._append_entries_processing(request)

        return sirius_cyber_corp.AppendEntries_1.Response(term=self._term, success=True)

    def _append_entries_processing(
        self,
        request: sirius_cyber_corp.AppendEntries_1.Request,
    ) -> None:
        """
        This method is called when the append entries request passes all checks and can be appended to the log.
        """
        assert len(request.log_entry) == 1  # in our implementation only a single entry is sent at a time

        # If an existing entry conflicts with a new one (same index but different terms),
        # delete the existing entry and all that follow it (§5.3)
        new_index = request.prev_log_index + 1
        _logger.info(c["append_entries"] + "Node ID: %d -- new_index: %d" + c["end_color"], self._node.id, new_index)
        for log_index, log_entry in enumerate(self._log[1:]):
            if (
                log_index + 1  # index + 1 because we skip the first entry (self._log[1:])
            ) == new_index and log_entry.term != request.log_entry[0].term:
                _logger.info(
                    c["append_entries"] + "Node ID: %d -- deleting from: %d" + c["end_color"],
                    self._node.id,
                    log_index + 1,
                )
                del self._log[log_index + 1 :]
                self._commit_index = log_index
                break

        # Append any new entries not already in the log
        # [in our implementation only a single entry is sent at a time]
        # 1. Check if the entry already exists
        append_new_entry = True
        if (
            new_index < len(self._log)
            and self._log[new_index].term == request.log_entry[0].term
            and self._log[new_index].entry.value == request.log_entry[0].entry.value
        ):  # log comparison can be done better, once this is fixed: https://github.com/OpenCyphal/pycyphal/issues/297
            append_new_entry = False
            _logger.info(c["append_entries"] + "Node ID: %d -- entry already exists" + c["end_color"], self._node.id)
        # 2. If it does not exist, append it
        if append_new_entry:
            if new_index < len(self._log):
                _logger.info("new_index < len(self._log): %s", new_index < len(self._log))
                _logger.info(
                    "self._log[new_index] == request.log_entry[0]: %s", self._log[new_index] == request.log_entry[0]
                )
                assert False
            self._log.append(request.log_entry[0])
            self._commit_index += 1
            _logger.info(
                c["append_entries"] + "Node ID: %d -- appended: %s" + c["end_color"],
                self._node.id,
                request.log_entry[0],
            )
            _logger.info(
                c["append_entries"] + "Node ID: %d -- commit_index: %d" + c["end_color"],
                self._node.id,
                self._commit_index,
            )

        # If leaderCommit > commitIndex, set commitIndex =
        # min(leaderCommit, index of last new entry)
        # Note: request.leader_commit can be less than self._commit_index if
        #       the leader is behind and is sending old entries
        # TODO: test this case (log replication)
        if request.leader_commit > self._commit_index:
            self._commit_index = min(request.leader_commit, new_index)

        # Update current_term
        self._term = request.log_entry[0].term

    async def _send_heartbeat(self) -> None:
        """
        This method is called periodically to send a heartbeat to all nodes in the cluster.
        It can fail in one of the following ways:
        - if the remote node has a higher term, the leader must convert to a follower
        - if the remote node is behind, the leader must send the missing entries
        - if the remote node is unreachable, the leader must retry later
        """
        # Send heartbeat to all other nodes
        #   - if response is true, update next_index and match_index
        #   - if response is false
        #      - if response term is greater than current_term, convert to follower
        #      - if term is equal to current_term, decrease prev_log_index, update next_index and retry
        # TODO: If response is false, implement the case where the follower is behind and needs to catch up

        assert self._state == RaftState.LEADER, "Only the leader can send heartbeats"

        # Send heartbeat to all other nodes
        for remote_node_index, remote_client in enumerate(self._append_entries_clients):
            remote_node_id = self._cluster[remote_node_index]
            _logger.info(
                c["raft_logic"] + "Node ID: %d -- Sending heartbeat to node %d" + c["end_color"],
                self._node.id,
                remote_node_id,
            )
            request = sirius_cyber_corp.AppendEntries_1.Request(
                term=self._term,
                prev_log_index=self._commit_index,
                prev_log_term=self._log[self._commit_index].term,
                log_entry=None,  # heartbeat has no log entry
            )
            _logger.info(
                c["raft_logic"] + "Node ID: %d -- prev_log_index: %d, prev_log_term: %d" + c["end_color"],
                self._node.id,
                request.prev_log_index,
                request.prev_log_term,
            )

            assert len(request.log_entry) == 0, "Heartbeat should not have a log entry"
            response = await remote_client(request)  # metadata is filled out by the client
            if response:
                if response.success:
                    _logger.info(
                        c["raft_logic"] + "Node ID: %d -- Heartbeat to node %d successful" + c["end_color"],
                        self._node.id,
                        remote_node_id,
                    )
                else:
                    if response.term > self._term:
                        _logger.info(
                            c["raft_logic"]
                            + "Node ID: %d -- Heartbeat to node %d failed (remote term > local term)"
                            + c["end_color"],
                            self._node.id,
                            remote_node_id,
                        )
                        self._change_state(RaftState.FOLLOWER)
                        self._voted_for = None
                        return
                    elif response.term == self._term:
                        _logger.info(
                            c["raft_logic"]
                            + "Node ID: %d -- Heartbeat to node %d failed (Log mismatch)"
                            + c["end_color"],
                            self._node.id,
                            remote_node_id,
                        )
            else:
                _logger.info(
                    c["raft_logic"] + "Node ID: %d -- Heartbeat to node %d failed (No response)" + c["end_color"],
                    self._node.id,
                    remote_node_id,
                )

        # for index, remote_node in enumerate(self._cluster):
        #     if remote_node._node.id == self._node.id:
        #         self.last_message_timestamp = time.time()
        #         self._next_index[index] = self._commit_index + 1
        #     else:
        #         prev_log_index = self._commit_index
        #         while prev_log_index >= 0:
        #             _logger.info(
        #                 "Node ID: %d -- Sending heartbeat to node %d, prev_log_index: %d",
        #                 self._node.id,
        #                 remote_node._node.id,
        #                 prev_log_index,
        #             )
        #             empty_topic_log = sirius_cyber_corp.LogEntry_1(
        #                 term=self._term,
        #                 entry=None,
        #             )
        #             request = sirius_cyber_corp.AppendEntries_1.Request(
        #                 term=self._term,
        #                 prev_log_index=prev_log_index,
        #                 prev_log_term=self._log[prev_log_index].term,
        #                 log_entry=empty_topic_log,
        #             )
        #             metadata = pycyphal.presentation.ServiceRequestMetadata(
        #                 client_node_id=self._node.id,  # leader's node id
        #                 timestamp=time.time(),
        #                 priority=0,
        #                 transfer_id=0,
        #             )
        #             response = await remote_node._serve_append_entries(request, metadata)
        #             if response.success:
        #                 _logger.info("Node ID: %d -- Heartbeat successful", self._node.id)
        #                 self._next_index[index] = prev_log_index + 1
        #                 break
        #             else:
        #                 _logger.info("Node ID: %d -- Heartbeat failed", self._node.id)
        #                 if response.term > self._term:
        #                     _logger.info("Node ID: %d -- Term mismatch, converting to follower", self._node.id)
        #                     # self._term = response.term
        #                     self._prev_state = self._state
        #                     _logger.info("Node ID: %d -- prev_state: %s", self._node.id, self._prev_state)
        #                     self._state = RaftState.FOLLOWER
        #                     self._voted_for = None
        #                     return
        #                 elif response.term == self._term:
        #                     _logger.info("Incomplete log on remote node, decreasing prev_log_index")
        #                     prev_log_index -= 1

    @staticmethod
    async def _serve_execute_command(
        request: uavcan.node.ExecuteCommand_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> uavcan.node.ExecuteCommand_1.Response:
        _logger.info("Execute command request %s from node %d", request, metadata.client_node_id)
        if request.command == uavcan.node.ExecuteCommand_1.Request.COMMAND_FACTORY_RESET:
            try:
                os.unlink(RaftNode.REGISTER_FILE)  # Reset to defaults by removing the register file.
            except OSError:  # Do nothing if already removed.
                pass
            return uavcan.node.ExecuteCommand_1.Response(uavcan.node.ExecuteCommand_1.Response.STATUS_SUCCESS)
        return uavcan.node.ExecuteCommand_1.Response(uavcan.node.ExecuteCommand_1.Response.STATUS_BAD_COMMAND)

    def _change_state(self, new_state: RaftState) -> None:
        """
        This method is used to change the state of the node.
        It will also take care of setting/cancelling the term timer upon changing states.
        FOLLOWER:
            - Term timer: NO
            - Election timer: YES
        CANDIDATE:
            - Term timer: NO
            - Election timer: NO
        LEADER:
            - Term timer: YES
            - Election timer: NO
        """
        self._prev_state = self._state
        self._state = new_state

        _logger.info("Node ID: %d -- Changing state from %s to %s", self._node.id, self._prev_state, self._state)

        if self._state == RaftState.FOLLOWER:
            # assert (
            #     self._prev_state == RaftState.LEADER or self._prev_state == RaftState.CANDIDATE
            # ), "Invalid state change 1"
            # Cancel the term timeout (if it exists), and schedule a new election timeout.
            if hasattr(self, "_term_timer"):
                self._term_timer.cancel()
            self._reset_election_timeout()
        elif self._state == RaftState.CANDIDATE:
            assert self._prev_state == RaftState.FOLLOWER, "Invalid state change 2"
            # Cancel the term timeout (if it exists), and cancel the election timeout (if it exists).
            if hasattr(self, "_term_timer"):
                self._term_timer.cancel()
            if hasattr(self, "_election_timer"):
                self._election_timer.cancel()
        elif self._state == RaftState.LEADER:
            assert self._prev_state == RaftState.CANDIDATE, "Invalid state change 3"
            # Cancel the election timeout (if it exists), and schedule a new term timeout.
            if hasattr(self, "_election_timer"):
                self._election_timer.cancel()
            self._reset_term_timeout()
        else:
            assert False, "Invalid state change"

    def _reset_election_timeout(self) -> None:
        """
        If a follower receives a heartbeat from the leader, it should reset its election timeout.
        """
        assert self._state == RaftState.FOLLOWER, "Only followers should reset the election timeout"
        _logger.info(c["raft_logic"] + "Node ID: %d -- Resetting election timeout" + c["end_color"], self._node.id)
        loop = asyncio.get_event_loop()
        self._next_election_timeout = loop.time() + self._election_timeout
        if hasattr(self, "_election_timer"):
            self._election_timer.cancel()
        self._election_timer = loop.call_at(
            self._next_election_timeout, asyncio.ensure_future, self._on_election_timeout()
        )
        _logger.info(
            c["raft_logic"] + "Node ID: %d -- Election timeout set to %f" + c["end_color"],
            self._node.id,
            self._next_election_timeout,
        )

    def _reset_term_timeout(self) -> None:
        """
        Once a term timeout is reached, another term callback is scheduled.
        """
        assert self._state == RaftState.LEADER, "Only leaders should reset the term timeout"
        _logger.info(c["raft_logic"] + "Node ID: %d -- Resetting term timeout" + c["end_color"], self._node.id)
        loop = asyncio.get_event_loop()
        self._next_term_timeout = loop.time() + self._term_timeout
        self._term_timer.cancel()
        self._term_timer = loop.call_at(self._next_term_timeout, asyncio.ensure_future, self._on_term_timeout())

    async def _on_election_timeout(self) -> None:
        """
        This function is called upon election timeout.
        The node starts an election and then restarts the election timeout.
        """
        assert self._state == RaftState.FOLLOWER, "Only followers have an election timeout"
        _logger.info(c["raft_logic"] + "Node ID: %d -- Election timeout reached" + c["end_color"], self._node.id)
        self._change_state(RaftState.CANDIDATE)
        await self._start_election()

    async def _on_term_timeout(self) -> None:
        """
        This function is called upon term timeout.
        If the node is a leader, it will send a heartbeat to all nodes in the cluster.
        """
        _logger.info(
            c["raft_logic"] + "Node ID: %d -- Term timeout reached, new term: %d" + c["end_color"],
            self._node.id,
            self._term,
        )
        assert self._state == RaftState.LEADER, "Only leaders have a term timeout"
        self._term += 1
        self._reset_term_timeout()
        await self._send_heartbeat()  # send heartbeat to all nodes in cluster (to update term)

    async def run(self) -> None:
        """
        This method will schedule the election and term timeouts.
        Upon timeout another callback is scheduled, which will allow the node to keep running.
        """
        _logger.info("Application Node started!")
        _logger.info("Running. Press Ctrl+C to stop.")

        loop = asyncio.get_event_loop()

        # Schedule election timeout (if follower or candidate)
        if self._state == RaftState.FOLLOWER:
            self._next_election_timeout = loop.time() + self._election_timeout
            self._election_timer = loop.call_at(
                self._next_election_timeout, asyncio.ensure_future, self._on_election_timeout()
            )

        # Schedule term timeout (only for leader)
        if self._state == RaftState.LEADER:
            self._next_term_timeout = loop.time() + self._term_timeout
            self._term_timer = loop.call_at(self._next_term_timeout, asyncio.ensure_future, self._on_term_timeout())

    def close(self) -> None:
        """
        Cancel the timers and close the node.
        """
        if hasattr(self, "_election_timer"):
            self._election_timer.cancel()
            assert self._election_timer.cancelled()
        if hasattr(self, "_term_timer"):
            self._term_timer.cancel()
            assert self._term_timer.cancelled()
        self._node.close()
