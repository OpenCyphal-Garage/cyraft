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

# ANSI colors for logging
c = {
    "end_color": "\033[0m",
    "general": "\033[36m",  # CYAN
    "request_vote": "\033[31m",  # RED
    "append_entries": "\033[32m",  # GREEN
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
        self.prev_state: RaftState = RaftState.FOLLOWER  # for testing purposes
        self.state: RaftState = RaftState.FOLLOWER

        self._election_timer: asyncio.TimerHandle
        self._election_timeout_task: asyncio.Task
        self._next_election_timeout: float
        self._term_timer: asyncio.TimerHandle
        self._next_term_timeout: float
        self._election_timeout: float = 0.15 + 0.15 * os.urandom(1)[0] / 255.0  # random between 150 and 300 ms
        self._term_timeout = TERM_TIMEOUT

        self.cluster: typing.List[int] = []
        self.request_vote_clients: typing.List[pycyphal.application.Client] = []
        self.append_entries_clients: typing.List[pycyphal.application.Client] = []

        ## Persistent state on all servers
        self.current_term: int = 0
        self.voted_for: int | None = None
        self.log: typing.List[sirius_cyber_corp.LogEntry_1] = []
        # index 0 contains an empty entry (so that the first entry starts at index 1 as per Raft paper)
        self.log.append(
            sirius_cyber_corp.LogEntry_1(
                term=0,
                entry=None,
            )
        )

        ## Volatile state on all servers
        self.commit_index: int = 0
        # self.last_applied: int = 0 # QUESTION: Is this even necessary? Do we have a "state machine"?

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
        if isinstance(remote_node_id, int):
            remote_node_id = [remote_node_id]

        for node_id in remote_node_id:
            if node_id not in self.cluster and node_id != self._node.id:
                _logger.info(c["general"] + f"Adding node {node_id} to cluster" + c["end_color"])
                self.cluster.append(node_id)
                request_vote_client = self._node.make_client(sirius_cyber_corp.RequestVote_1, node_id, "request_vote")
                self.request_vote_clients.append(request_vote_client)
                append_entries_client = self._node.make_client(
                    sirius_cyber_corp.AppendEntries_1, node_id, "append_entries"
                )
                self.append_entries_clients.append(append_entries_client)
                self.next_index.append(1)
                self.match_index.append(0)

        total_nodes = len(self.cluster)
        assert len(self.request_vote_clients) == total_nodes
        assert len(self.append_entries_clients) == total_nodes
        assert len(self.next_index) == total_nodes
        assert len(self.match_index) == total_nodes

    def remove_remote_node(self, remote_node_id: int) -> None:
        if remote_node_id in self.cluster:
            _logger.info(c["general"] + f"Removing node {remote_node_id} from cluster" + c["end_color"])
            index = self.cluster.index(remote_node_id)
            self.cluster.pop(index)
            self.request_vote_clients.pop(index)
            self.append_entries_clients.pop(index)
            self.next_index.pop(index)
            self.match_index.pop(index)

    async def _serve_request_vote_impl(
        self, request: sirius_cyber_corp.RequestVote_1.Request, client_node_id: int
    ) -> sirius_cyber_corp.RequestVote_1.Response:
        # Reply false if term < self.current_term (§5.1)
        if request.term < self.current_term or self.voted_for is not None:
            _logger.info(
                c["request_vote"]
                + "Request vote request denied (term < self.current_term or self.voted_for is not None))"
                + c["end_color"]
            )
            return sirius_cyber_corp.RequestVote_1.Response(term=self.current_term, vote_granted=False)

        # If voted_for is null or candidateId, and candidate’s log is at
        # least as up-to-date as receiver’s log, grant vote (§5.2, §5.4) # TODO: implement log comparison
        elif self.voted_for is None or self.voted_for == client_node_id:
            # log comparison
            if self.log[request.last_log_index].term == request.last_log_term:
                _logger.info(c["request_vote"] + "Request vote request granted" + c["end_color"])
                self.voted_for = client_node_id
                self.current_term = request.term
                return sirius_cyber_corp.RequestVote_1.Response(
                    term=self.current_term,
                    vote_granted=True,
                )
            else:
                _logger.info(c["request_vote"] + "Request vote request denied (failed log comparison)" + c["end_color"])
                return sirius_cyber_corp.RequestVote_1.Response(term=self.current_term, vote_granted=False)

        assert False, "Should not reach here!"

    async def _serve_request_vote(
        self,
        request: sirius_cyber_corp.RequestVote_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> sirius_cyber_corp.RequestVote_1.Response:
        _logger.info(
            c["request_vote"] + "Node ID: %d -- Request vote request %s from node %d" + c["end_color"],
            self._node.id,
            request,
            metadata.client_node_id,
        )
        response = await self._serve_request_vote_impl(request, metadata.client_node_id)
        return response

    async def _start_election(self) -> None:
        _logger.info(c["general"] + "Node ID: %d -- Starting election" + c["end_color"], self._node.id)
        # Increment currentTerm
        self.current_term += 1
        # Vote for self
        self.voted_for = self._node.id
        # Reset election timeout
        self.last_message_timestamp = time.time()
        # Send RequestVote RPCs to all other servers
        last_log_index = len(self.log) - 1  # if log is empty (only entry is at index zero), last_log_index = 0
        request = sirius_cyber_corp.RequestVote_1.Request(
            term=self.current_term,
            last_log_index=last_log_index,
            last_log_term=self.log[last_log_index].term,
        )
        # Send request vote to all nodes in cluster, count votes
        number_of_nodes = len(self.cluster) + 1  # +1 for self
        number_of_votes = 1  # Vote for self
        for remote_node_index, remote_client in enumerate(self.request_vote_clients):  # index allows to find node id
            remote_node_id = self.cluster[remote_node_index]
            _logger.info(
                c["general"] + "Node ID: %d -- Sending request vote to node %d" + c["end_color"],
                self._node.id,
                remote_node_id,
            )
            response = await remote_client(request)
            if response:
                _logger.info(
                    c["general"] + "Node ID: %d -- Response from node %d: %s" + c["end_color"],
                    self._node.id,
                    remote_node_id,
                    response,
                )
                if response.vote_granted:
                    number_of_votes += 1
            else:
                _logger.info(
                    c["general"] + "Node ID: %d -- No response from node %d" + c["end_color"],
                    self._node.id,
                    remote_node_id,
                )

        # If votes received from majority of servers: become leader
        if number_of_votes > number_of_nodes / 2:  # int(5/2) = 2, int(3/2) = 1
            _logger.info(c["general"] + "Node ID: %d -- Became leader" + c["end_color"], self._node.id)
            self.prev_state = self.state
            self.state = RaftState.LEADER
        else:
            _logger.info(c["general"] + "Node ID: %d -- Election failed" + c["end_color"], self._node.id)
            # If AppendEntries RPC received from new leader: convert to follower
            # reset election timeout
            self.last_message_timestamp = time.time()
            self.voted_for = None
            # convert to follower
            self.prev_state = self.state
            self.state = RaftState.FOLLOWER

    async def _serve_append_entries(
        self,
        request: sirius_cyber_corp.AppendEntries_1.Request,
        metadata: pycyphal.presentation.ServiceRequestMetadata,
    ) -> sirius_cyber_corp.AppendEntries_1.Response:
        _logger.info(
            c["append_entries"] + "Node ID: %d -- Append entries request %s from node %d" + c["end_color"],
            self._node.id,
            request,
            metadata.client_node_id,
        )

        assert (
            type(request.log_entry) == np.ndarray or type(request.log_entry) == list or request.log_entry == None
        ), "log_entry must be a numpy array, list or None"

        # heartbeat processing
        # if request.log_entry == None:
        if len(request.log_entry) == 0:  # empty means heartbeat
            if request.term < self.current_term:
                _logger.info(
                    c["append_entries"] + "Node ID: %d -- Heartbeat denied (term < currentTerm)" + c["end_color"],
                    self._node.id,
                )
                return sirius_cyber_corp.AppendEntries_1.Response(term=self.current_term, success=False)
            else:  # request.term >= self.current_term
                _logger.info(c["append_entries"] + "Node ID: %d -- Heartbeat received" + c["end_color"], self._node.id)
                if metadata.client_node_id != self.voted_for:
                    _logger.info(
                        c["append_entries"] + "Node ID: %d -- Heartbeat from new leader" + c["end_color"], self._node.id
                    )
                    self.voted_for = metadata.client_node_id
                    self.prev_state = self.state
                    self.state = RaftState.FOLLOWER
                self.last_message_timestamp = time.time()  # reset election timeout
                self.current_term = request.term  # update term
                return sirius_cyber_corp.AppendEntries_1.Response(term=self.current_term, success=True)

        # Reply false if term < currentTerm (§5.1)
        if request.term < self.current_term:
            _logger.info(c["append_entries"] + "Append entries request denied (term < currentTerm)" + c["end_color"])
            return sirius_cyber_corp.AppendEntries_1.Response(term=self.current_term, success=False)

        # Reply false if log doesn’t contain an entry at prevLogIndex
        # whose term matches prevLogTerm (§5.3)
        try:
            if self.log[request.prev_log_index].term != request.prev_log_term:
                _logger.info(c["append_entries"] + "Append entries request denied (log mismatch)" + c["end_color"])
                return sirius_cyber_corp.AppendEntries_1.Response(term=self.current_term, success=False)
        except IndexError:
            _logger.info(c["append_entries"] + "Append entries request denied (log mismatch 2)" + c["end_color"])
            return sirius_cyber_corp.AppendEntries_1.Response(term=self.current_term, success=False)

        self._append_entries_processing(request)

        return sirius_cyber_corp.AppendEntries_1.Response(term=self.current_term, success=True)

    def _append_entries_processing(
        self,
        request: sirius_cyber_corp.AppendEntries_1.Request,
    ) -> None:
        assert len(request.log_entry) == 1  # in our implementation only a single entry is sent at a time
        # If an existing entry conflicts with a new one (same index
        # but different terms), delete the existing entry and all that
        # follow it (§5.3)
        new_index = request.prev_log_index + 1
        _logger.debug(c["append_entries"] + "new_index: %d" + c["end_color"], new_index)
        for log_index, log_entry in enumerate(self.log[1:]):
            if (
                log_index + 1  # index + 1 because we skip the first entry (self.log[1:])
            ) == new_index and log_entry.term != request.log_entry[0].term:
                _logger.debug(c["append_entries"] + "deleting from: %d" + c["end_color"], log_index + 1)
                del self.log[log_index + 1 :]
                self.commit_index = log_index
                break

        # Append any new entries not already in the log
        # [in our implementation only a single entry is sent at a time]
        # 1. Check if the entry already exists
        append_new_entry = True
        if new_index < len(self.log) and self.log[new_index] == request.log_entry[0]:
            append_new_entry = False
            _logger.debug(c["append_entries"] + "entry already exists" + c["end_color"])
        # 2. If it does not exist, append it
        if append_new_entry:
            self.log.append(request.log_entry[0])
            self.commit_index += 1
            _logger.debug(c["append_entries"] + "appended: %s" + c["end_color"], request.log_entry[0])
            _logger.debug(c["append_entries"] + "commit_index: %d" + c["end_color"], self.commit_index)

        # If leaderCommit > commitIndex, set commitIndex =
        # min(leaderCommit, index of last new entry)
        # Note: request.leader_commit can be less than self.commit_index if
        #       the leader is behind and is sending old entries
        # TODO: test this case (log replication)
        if request.leader_commit > self.commit_index:
            self.commit_index = min(request.leader_commit, new_index)

        # Update current_term
        self.current_term = request.log_entry[0].term

    async def _send_heartbeat(self) -> None:
        # 1. "Send" heartbeat to itself (i.e. process it locally)
        # 2. Send heartbeat to all other nodes
        #    - if response is true, update next_index and match_index
        #    - if response is false
        #       - if response term is greater than current_term, convert to follower
        #       - if term is equal to current_term, decrease prev_log_index, update next_index and retry
        # TODO: If response is false, implement the case where the follower is behind and needs to catch up

        # 1. "Send" heartbeat to itself (i.e. process it locally)
        self.last_message_timestamp = time.time()

        # 2. Send heartbeat to all other nodes
        for remote_node_index, remote_client in enumerate(self.append_entries_clients):
            remote_node_id = self.cluster[remote_node_index]
            _logger.info(
                c["general"] + "Node ID: %d -- Sending heartbeat to node %d" + c["end_color"],
                self._node.id,
                remote_node_id,
            )
            empty_topic_log = sirius_cyber_corp.LogEntry_1(
                term=self.current_term,
                entry=None,
            )
            request = sirius_cyber_corp.AppendEntries_1.Request(
                term=self.current_term,
                prev_log_index=self.commit_index,
                prev_log_term=self.log[self.commit_index].term,
                log_entry=empty_topic_log,
            )
            metadata = sirius_cyber_corp.Metadata(
                client_node_id=self._node.id,  # leader's node id
                timestamp=time.time(),
                priority=0,
                transfer_id=0,
            )
            response = await remote_client(request, metadata=metadata)
            if response:
                if response.success:
                    _logger.info(
                        c["general"] + "Node ID: %d -- Heartbeat to node %d successful" + c["end_color"],
                        self._node.id,
                        remote_node_id,
                    )
                else:
                    if response.term > self.current_term:
                        _logger.info(
                            c["general"]
                            + "Node ID: %d -- Heartbeat to node %d failed (Term mismatch)"
                            + c["end_color"],
                            self._node.id,
                            remote_node_id,
                        )
                        self._node.prev_state = self._node.state
                        self._node.state = RaftState.FOLLOWER
                        self.voted_for = None
                        return
                    elif response.term == self.current_term:
                        _logger.info(
                            c["general"] + "Node ID: %d -- Heartbeat to node %d failed (Log mismatch)" + c["end_color"],
                            self._node.id,
                            remote_node_id,
                        )
                        _logger.info(
                            c["general"] + "Incomplete log on remote node, decreasing prev_log_index" + c["end_color"]
                        )
                        prev_log_index -= 1
            else:
                _logger.info(
                    c["general"] + "Node ID: %d -- Heartbeat to node %d failed (No response)" + c["end_color"],
                    self._node.id,
                    remote_node_id,
                )

        # for index, remote_node in enumerate(self.cluster):
        #     if remote_node._node.id == self._node.id:
        #         self.last_message_timestamp = time.time()
        #         self.next_index[index] = self.commit_index + 1
        #     else:
        #         prev_log_index = self.commit_index
        #         while prev_log_index >= 0:
        #             _logger.info(
        #                 "Node ID: %d -- Sending heartbeat to node %d, prev_log_index: %d",
        #                 self._node.id,
        #                 remote_node._node.id,
        #                 prev_log_index,
        #             )
        #             empty_topic_log = sirius_cyber_corp.LogEntry_1(
        #                 term=self.current_term,
        #                 entry=None,
        #             )
        #             request = sirius_cyber_corp.AppendEntries_1.Request(
        #                 term=self.current_term,
        #                 prev_log_index=prev_log_index,
        #                 prev_log_term=self.log[prev_log_index].term,
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
        #                 self.next_index[index] = prev_log_index + 1
        #                 break
        #             else:
        #                 _logger.info("Node ID: %d -- Heartbeat failed", self._node.id)
        #                 if response.term > self.current_term:
        #                     _logger.info("Node ID: %d -- Term mismatch, converting to follower", self._node.id)
        #                     # self.current_term = response.term
        #                     self.prev_state = self.state
        #                     _logger.info("Node ID: %d -- prev_state: %s", self._node.id, self.prev_state)
        #                     self.state = RaftState.FOLLOWER
        #                     self.voted_for = None
        #                     return
        #                 elif response.term == self.current_term:
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

    def _reset_election_timeout(self) -> None:
        _logger.info(c["general"] + "Node ID: %d -- Resetting election timeout" + c["end_color"], self._node.id)
        loop = asyncio.get_event_loop()
        self._next_election_timeout = loop.time() + self._election_timeout
        self._election_timer.cancel()
        # self._election_timer = loop.call_at(self._next_election_timeout, self._call_on_election_timeout)
        self._election_timer = loop.call_at(
            self._next_election_timeout, asyncio.ensure_future, self._on_election_timeout()
        )

    # def _call_on_election_timeout(self):
    #     loop = asyncio.get_event_loop()

    #     # Save the task to avoid the task disappearing, see https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
    #     self._election_timeout_task = loop.create_task(self._on_election_timeout)

    #     await self._election_timeout_task

    #     # loop.call_soon_threadsafe(self._election_timeout_task) # Tried to force the task like this as well (even though it shouldn't be necessary since create_task documentation clearly states it will schedule the task) but doesn't work either.

    async def _on_election_timeout(self) -> None:
        if self.state == RaftState.FOLLOWER or self.state == RaftState.CANDIDATE:
            _logger.info(c["general"] + "Node ID: %d -- Election timeout reached" + c["end_color"], self._node.id)
            self.prev_state = self.state
            self.state = RaftState.CANDIDATE
            # loop = asyncio.get_event_loop()
            # loop.call_soon_threadsafe(asyncio.create_task, self._start_election())
            # loop.call_soon_threadsafe(self._start_election)
            await self._start_election()
            self._reset_election_timeout()
        elif self.state == RaftState.LEADER:
            # heartbeat send every term timeout should make sure no election timeout happens
            pass
        else:
            assert False, "Invalid state"

    def _reset_term_timeout(self) -> None:
        _logger.info(c["general"] + "Node ID: %d -- Resetting term timeout" + c["end_color"], self._node.id)
        loop = asyncio.get_event_loop()
        self._next_term_timeout = loop.time() + self._term_timeout
        self._term_timer.cancel()
        self._term_timer = loop.call_at(self._next_term_timeout, self._on_term_timeout)

    def _on_term_timeout(self) -> None:
        if self.state == RaftState.LEADER:
            _logger.info(c["general"] + "Node ID: %d -- Term timeout reached" + c["end_color"], self._node.id)
            self.current_term += 1
            # send heartbeat to all nodes in cluster (to update term)
            # await self._send_heartbeat()
            self._reset_term_timeout()
            # await self._reset_election_timeout()
        elif self.state == RaftState.CANDIDATE:
            self.current_term += 1
        elif self.state == RaftState.FOLLOWER:
            # term is updated by the leader, and then sent to all nodes in the cluster
            pass
        else:
            assert False, "Invalid state"

    async def run(self) -> None:
        """
        The main method that runs the business logic. It is also possible to use the library in an IoC-style
        by using receive_in_background() for all subscriptions if desired.
        """
        _logger.info("Application Node started!")
        _logger.info("Running. Press Ctrl+C to stop.")

        loop = asyncio.get_event_loop()
        self._next_election_timeout = loop.time() + self._election_timeout
        # self._election_timer = loop.call_at(self._next_election_timeout, self._on_election_timeout)
        self._election_timer = loop.call_at(
            self._next_election_timeout, asyncio.ensure_future, self._on_election_timeout()
        )
        self._next_term_timeout = loop.time() + self._term_timeout
        self._term_timer = loop.call_at(self._next_term_timeout, self._on_term_timeout)

    def close(self) -> None:
        """
        This will close all the underlying resources down to the transport interface and all publishers/servers/etc.
        All pending tasks such as serve_in_background()/receive_in_background() will notice this and exit automatically.
        """
        # self._election_timer.cancel()
        self._term_timer.cancel()
        self._node.close()


# ----------------------------------------  TESTS GO BELOW THIS LINE  ----------------------------------------


async def _unittest_raft_node_heartbeat() -> None:
    """
    Test that the node does NOT convert to candidate if it receives a heartbeat message
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    raft_node.voted_for = 42
    raft_node.election_timeout = ELECTION_TIMEOUT

    asyncio.create_task(raft_node.run())
    await asyncio.sleep(ELECTION_TIMEOUT * 0.90)  # sleep until right before election timeout

    # send heartbeat
    terms_passed = raft_node.current_term
    await raft_node._serve_append_entries(
        sirius_cyber_corp.AppendEntries_1.Request(
            term=terms_passed,  # leader's term is the same as the follower's term (can also be higher)
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
    await asyncio.sleep(ELECTION_TIMEOUT * 0.1)
    assert raft_node.state == RaftState.FOLLOWER
    assert raft_node.voted_for == 42
    # last_message_timestamp should be updated
    assert (time.time() - raft_node.last_message_timestamp) < (0.2 * raft_node.election_timeout)

    ## test that the node converts to candidate after the election timeout [if no heartbeat is received]
    await asyncio.sleep(ELECTION_TIMEOUT)
    assert raft_node.prev_state == RaftState.CANDIDATE

    raft_node.close()
    await asyncio.sleep(1)  # fixes when just running this test, however not when "pytest /cyraft" is run


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


async def _unittest_raft_node_start_election() -> None:
    """
    Test the _start_election method

    Using two nodes, one has a shorter election timeout than the other.
    """
    os.environ["UAVCAN__NODE__ID"] = "41"
    os.environ["UAVCAN__SRV__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__CLN__REQUEST_VOTE__ID"] = "1"
    os.environ["UAVCAN__SRV__APPEND_ENTRIES__ID"] = "2"
    os.environ["UAVCAN__CLN__APPEND_ENTRIES__ID"] = "2"
    raft_node_1 = RaftNode()
    raft_node_1.election_timeout = ELECTION_TIMEOUT
    os.environ["UAVCAN__NODE__ID"] = "42"
    raft_node_2 = RaftNode()
    raft_node_2.election_timeout = ELECTION_TIMEOUT * 2

    # raft_node_1.cluster = [raft_node_1, raft_node_2]
    # raft_node_2.cluster = [raft_node_1, raft_node_2]

    cluster_nodes = [raft_node_1._node.id, raft_node_2._node.id]
    assert cluster_nodes == [41, 42]

    raft_node_1.add_remote_node(cluster_nodes)
    raft_node_2.add_remote_node(cluster_nodes)

    asyncio.create_task(raft_node_1.run())
    asyncio.create_task(raft_node_2.run())

    await asyncio.sleep(ELECTION_TIMEOUT)

    # test 1: node 1 should become candidate
    assert raft_node_1.state == RaftState.LEADER
    assert raft_node_1.prev_state == RaftState.CANDIDATE
    assert raft_node_1.voted_for == 41

    # test 2: node 2 should become follower
    assert raft_node_2.state == RaftState.FOLLOWER
    assert raft_node_2.voted_for == 41

    # test 3: node 1 should send a heartbeat to node 2, remain leader
    await asyncio.sleep(ELECTION_TIMEOUT)

    assert raft_node_1.state == RaftState.LEADER

    raft_node_1.close()
    raft_node_2.close()
    await asyncio.sleep(1)


async def _unittest_raft_node_append_entries_rpc() -> None:
    #
    # Step 1: Append 3 log entries
    #   ____________ ____________ ____________ ____________
    #  | 0          | 1          | 2          | 3          |     Log index
    #  | 0          | 4          | 5          | 6          |     Log term
    #  | empty <= 0 | top_1 <= 7 | top_2 <= 8 | top_3 <= 9 |     Name <= value
    #  |____________|____________|____________|____________|
    #
    # Step 2: Replace log entry 3 with a new entry
    #   ____________
    #  | 3          |     Log index
    #  | 7          |     Log term
    #  | top_3 <= 10|     Name <= value
    #  |____________|
    #
    # Step 3: Replace log entries 2 and 3 with new entries
    #   ____________ ____________
    #  | 2          | 3          |     Log index
    #  | 8          | 9          |     Log term
    #  | top_2 <= 11| top_3 <= 12|     Name <= value
    #  |____________|____________|
    #
    # Step 4: Add an additional log entry
    #   ____________
    #  | 4          |     Log index
    #  | 10         |     Log term
    #  | top_4 <= 13|     Name <= value
    #  |____________|
    #
    # Result:
    #   ____________ ____________ ____________ ____________ ____________
    #  | 0          | 1          | 2          | 3          | 4          |     Log index
    #  | 0          | 4          | 8          | 9          | 10         |     Log term
    #  | empty <= 0 | top_1 <= 7 | top_2 <= 10| top_3 <= 11| top_4 <= 13|     Name <= value
    #  |____________|____________|____________|____________|____________|
    #
    # Step 5: Try to append old log entry (term < currentTerm)
    #   ____________
    #  | 4          |     Log index
    #  | 9          |     Log term
    #  | top_4 <= 14|     Name <= value
    #  |____________|
    #
    # Step 6: Try to append valid log entry, however entry at prev_log_index term does not match
    #   ____________
    #  | 4          |     Log index
    #  | 11         |     Log term
    #  | top_4 <= 15|     Name <= value
    #  |____________|
    #
    #
    #  Every step check:
    #    - self.current_term
    #    - self.voted_for
    #    - self.log
    #    - self.commit_index

    ##### SETUP #####
    os.environ["UAVCAN__NODE__ID"] = "41"
    raft_node = RaftNode()
    raft_node.voted_for = 42
    index_zero_entry = sirius_cyber_corp.LogEntry_1(
        term=0,
        entry=None,
    )

    ##### STEP 1 #####
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
            prev_log_term=raft_node.log[index].term,
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

    assert raft_node.current_term == 6
    assert raft_node.voted_for == 42
    # assert raft_node.log == [index_zero_entry] + new_entries # TODO: How to compare log entries?
    # assert raft_node.log[0] == index_zero_entry
    assert len(raft_node.log) == 1 + 3
    assert raft_node.log[0].term == 0
    assert raft_node.log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node.log[0].entry.value == 0

    assert raft_node.log[1].term == 4
    assert raft_node.log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"  # This is kind of ugly?
    assert raft_node.log[1].entry.value == 7
    assert raft_node.log[2].term == 5
    assert raft_node.log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node.log[2].entry.value == 8
    assert raft_node.log[3].term == 6
    assert raft_node.log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node.log[3].entry.value == 9
    assert raft_node.commit_index == 3

    # Once https://github.com/OpenCyphal/pycyphal/issues/297 is fixed, we can do this:
    # assert raft_node.log[0] == index_zero_entry
    # assert raft_node.log[1] == new_entries[0]
    # assert raft_node.log[2] == new_entries[1]
    # assert raft_node.log[3] == new_entries[2]

    ##### STEP 2 #####
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
        prev_log_term=raft_node.log[2].term,
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

    assert raft_node.current_term == 7
    assert raft_node.voted_for == 42

    assert len(raft_node.log) == 1 + 3
    assert raft_node.log[0].term == 0
    assert raft_node.log[0].entry.name.value.tobytes().decode("utf-8") == ""  # index zero entry is empty
    assert raft_node.log[0].entry.value == 0

    assert raft_node.log[1].term == 4
    assert raft_node.log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node.log[1].entry.value == 7
    assert raft_node.log[2].term == 5
    assert raft_node.log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node.log[2].entry.value == 8
    assert raft_node.log[3].term == 7
    assert raft_node.log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node.log[3].entry.value == 10
    assert raft_node.commit_index == 3

    ##### STEP 3 #####
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
            prev_log_term=raft_node.log[index + 1].term,
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

    assert raft_node.current_term == 9
    assert raft_node.voted_for == 42

    assert len(raft_node.log) == 1 + 3
    assert raft_node.log[0].term == 0
    assert raft_node.log[0].entry.value == 0
    assert raft_node.log[1].term == 4
    assert raft_node.log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node.log[1].entry.value == 7
    assert raft_node.log[2].term == 8
    assert raft_node.log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node.log[2].entry.value == 11
    assert raft_node.log[3].term == 9
    assert raft_node.log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node.log[3].entry.value == 12

    ##### STEP 4 #####
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
        prev_log_term=raft_node.log[3].term,
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

    assert raft_node.current_term == 10
    assert raft_node.voted_for == 42

    assert len(raft_node.log) == 1 + 4
    assert raft_node.log[0].term == 0
    assert raft_node.log[0].entry.value == 0
    assert raft_node.log[1].term == 4
    assert raft_node.log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node.log[1].entry.value == 7
    assert raft_node.log[2].term == 8
    assert raft_node.log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node.log[2].entry.value == 11
    assert raft_node.log[3].term == 9
    assert raft_node.log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node.log[3].entry.value == 12
    assert raft_node.log[4].term == 10
    assert raft_node.log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node.log[4].entry.value == 13

    ##### STEP 5 #####
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
        prev_log_term=raft_node.log[3].term,
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

    assert raft_node.current_term == 10
    assert raft_node.voted_for == 42

    assert len(raft_node.log) == 1 + 4
    assert raft_node.log[0].term == 0
    assert raft_node.log[0].entry.value == 0
    assert raft_node.log[1].term == 4
    assert raft_node.log[1].entry.name.value.tobytes().decode("utf-8") == "top_1"
    assert raft_node.log[1].entry.value == 7
    assert raft_node.log[2].term == 8
    assert raft_node.log[2].entry.name.value.tobytes().decode("utf-8") == "top_2"
    assert raft_node.log[2].entry.value == 11
    assert raft_node.log[3].term == 9
    assert raft_node.log[3].entry.name.value.tobytes().decode("utf-8") == "top_3"
    assert raft_node.log[3].entry.value == 12
    assert raft_node.log[4].term == 10
    assert raft_node.log[4].entry.name.value.tobytes().decode("utf-8") == "top_4"
    assert raft_node.log[4].entry.value == 13

    ##### STEP 6 #####
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
        prev_log_term=raft_node.log[4].term - 1,  # term mismatch
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

    assert raft_node.current_term == 10
    assert raft_node.voted_for == 42
    # TODO: How to compare log entries?
    assert len(raft_node.log) == 1 + 4
    assert raft_node.log[0].term == 0
    assert raft_node.log[0].entry.value == 0
    assert raft_node.log[1].term == 4
    assert raft_node.log[1].entry.value == 7
    assert raft_node.log[2].term == 8
    assert raft_node.log[2].entry.value == 11
    assert raft_node.log[3].term == 9
    assert raft_node.log[3].entry.value == 12
    assert raft_node.log[4].term == 10
    assert raft_node.log[4].entry.value == 13
