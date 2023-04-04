# Cyraft

This is an exercise in implemeting the Raft algorithm, as it could be useful within pycyphal, in order to implement "named topics". The reason why we're interested in supporting "named topics": this could (eventually) be leveraged to allow Cyphal to function as a communication layer between PX4 and ROS. (See [UAVCAN as a middleware for ROS](https://forum.opencyphal.org/t/an-exploratory-study-uavcan-as-a-middleware-for-ros/872))

## TODO

- [x] Finish study pycyphal communication layer
- [ ] `demo_node.py`

## Graphs

```mermaid
---
title: cyraft node X
---
flowchart TB
    subgraph 1X:org.opencyphal.pycyphal.raft.node
        direction TB
        subgraph heartbeat_publisher
            direction TB
            heartbeat_publisher_1[/uavcan.node.Heartbeat.1.0\]
        end
        heartbeat_publisher --> uavcan.node.heartbeat
        subgraph RequestVoteRPC
            direction TB
            request_vote_1{{uavcan.node.RequestVoteRPC}}
        end
        10X:uavcan.node.RequestVote.Request --> RequestVoteRPC
        RequestVoteRPC --> 10X:uavcan.node.RequestVote.Response
        subgraph AppendEntriesRPC
            direction TB
            append_entries_1{{uavcan.node.AppendEntriesRPC}}
        end
        11X:uavcan.node.AppendEntriesRPC.Request --> AppendEntriesRPC
        AppendEntriesRPC --> 11X:uavcan.node.AppendEntriesRPC.Response
    end
```

DSDL datatypes

```mermaid
---
title: RequestVote
---
classDiagram
    class RequestVote_Request{
        -int term
        -int candidateID
        -int lastLogIndex
        -int lastLogTerm
    }

    class RequestVote_Response{
        -int term
        -bool voteGranted

    }
```

```mermaid
---
title: AppendEntries
---
classDiagram
    class AppendEntries_Request{
        -int term
        -int leaderID
        -int prevLogIndex
        -int prevLogIndex
        -int prevLogTerm
        -entry entry
        -int leaderCommit
    }

    class AppendEntries_Response{
        -int term
        -bool success

    }
```

## Sources

[Raft paper](https://raft.github.io/raft.pdf)

[lynix94/pyraft](https://github.com/lynix94/pyraft)

[zhebrak/raftos](https://github.com/zhebrak/raftos)

[dronecan/libuavcan](https://github.com/dronecan/libuavcan/tree/main/libuavcan/include/uavcan/protocol/dynamic_node_id_server/distributed)

[An exploratory study: UAVCAN as a middleware for ROS](https://forum.opencyphal.org/t/an-exploratory-study-uavcan-as-a-middleware-for-ros/872)

[Allocators explanation in OpenCyphal/public_regulated_data_types](https://github.com/OpenCyphal/public_regulated_data_types/blob/master/uavcan/pnp/8165.NodeIDAllocationData.2.0.dsdl)

