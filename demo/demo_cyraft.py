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

# DSDL files are automatically compiled by pycyphal import hook from sources pointed by CYPHAL_PATH env variable.
import sirius_cyber_corp  # This is our vendor-specific root namespace. Custom data types.
import pycyphal.application  # This module requires the root namespace "uavcan" to be transcompiled.

# Import other namespaces we're planning to use. Nested namespaces are not auto-imported, so in order to reach,
# say, "uavcan.node.Heartbeat", you have to "import uavcan.node".
import uavcan.node  # noqa

_logger = logging.getLogger(__name__)


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
        # latest term server has seen (initialized to 0 on first boot, increases monotonically)
        self.currentTerm: int = 5
        # candidateId that received vote in current term (or null if none)
        self.votedFor: int | None = None
        # log entries; each entry contains command for state machine,
        # and term when entry was received by leader
        self.log: typing.List[typing.Tuple[str, int]] = []

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

        # Reply false if term < currentTerm (§5.1)
        if request.term < self.currentTerm:
            return sirius_cyber_corp.RequestVote_1.Response(
                term=1, voteGranted=False
            )  # TODO: get term

        # If votedFor is null or candidateId, and candidate’s log is at
        # least as up-to-date as receiver’s log, grant vote (§5.2, §5.4)
        if self.votedFor is None or self.votedFor == request.candidate_id:
            return sirius_cyber_corp.RequestVote_1.Response(
                term=1,  # TODO: get term from self
                voteGranted=True,
            )

        _logger.error("Should not reach here!")

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
            await asyncio.sleep(1)

    def close(self) -> None:
        """
        This will close all the underlying resources down to the transport interface and all publishers/servers/etc.
        All pending tasks such as serve_in_background()/receive_in_background() will notice this and exit automatically.
        """
        self._node.close()


async def main() -> None:
    logging.root.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stderr)
    _logger.addHandler(handler)
    _logger.info("Starting the application...")
    app = RaftNode()
    try:
        await app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()


if __name__ == "__main__":
    asyncio.run(main())
