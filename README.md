# Cyraft

This is an exercise in implemeting the Raft algorithm, as it could be useful within pycyphal, in order to implement "named topics". The reason why we're interested in supporting "named topics": this could (eventually) be leveraged to allow Cyphal to function as a communication layer between PX4 and ROS. (See [UAVCAN as a middleware for ROS](https://forum.opencyphal.org/t/an-exploratory-study-uavcan-as-a-middleware-for-ros/872))

## TODO

- [x] Finish study pycyphal application layer
- [ ] `demo_node.py`
  - [ ] request_vote_rpc
    - [x] Add instructions on how to interact with request_vote_rpc using `yakut`
    - [ ] Implement `request_vote_rpc`
    - [ ] Add orchestration so there's 3 nodes running simultanously
    - [ ] Refactor code
  - [ ] append_entries_rpc


## Setup

- Clone repo

    ```bash
    git clone https://github.com/maksimdrachov/cyraft_project
    ```

- Virtual environment

    ```bash
    cd ~/cyraft
    python3 -m venv env
    source env/bin/activate
    ```
 
- Install requirements (pycyphal)

    ```bash
    cd ~/cyraft
    pip3 install -r requirements.txt
    ```

-   ```bash
    cd ~/cyraft/demo
    git clone https://github.com/OpenCyphal/public_regulated_data_types
    ```

-   ```bash
    export CYPHAL_PATH="$HOME/cyraft/demo/custom_data_types:$HOME/cyraft/demo/public_regulated_data_types"
    ```

- Set environment variables (registers)

    ```bash
    cd ~/cyraft
    source my_env.sh
    ```

- Run the demo

    ```bash
    python3 demo/demo_node.py
    ```

> **_NOTE:_**  Sometimes this can give an error if it's using old datatypes, try to remove ~/.pycyphal and recompile DSDL datatypes (running previous command will do this automatically)

    ```bash
    rm -rf ~/.pycyphal
    ```

### Request Vote RPC

While running the previous `demo_node.py`, in a new terminal window:

- Setup

    ```bash
    cd ~/cyraft
    source env/bin/activate
    export CYPHAL_PATH="$HOME/cyraft/demo/custom_data_types:$HOME/cyraft/demo/public_regulated_data_types"
    source my_env.sh
    export UAVCAN__UDP__IFACE=127.0.0.1
    export UAVCAN__NODE__ID=111
    ```

- Send an RPC to request_vote (using `yakut`)

    ```bash
    y q 42 request_vote_rpc '[1,1,1,1]'
    ```

    ![request-vote-rpc](images/request_vote_rpc.png)

## Diagrams

### demo_node

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
        subgraph request_vote_rpc
            direction TB
            request_vote_1{{sirius_cyber_corp.RequestVoteRPC}}
        end
        10X:sirius_cyber_corp.RequestVote.Request --> request_vote_rpc
        request_vote_rpc --> 10X:sirius_cyber_corp.RequestVote.Response
        subgraph append_entries_rpc
            direction TB
            append_entries_1{{sirius_cyber_corp.AppendEntriesRPC}}
        end
        11X:sirius_cyber_corp.AppendEntriesRPC.Request --> append_entries_rpc
        append_entries_rpc --> 11X:sirius_cyber_corp.AppendEntriesRPC.Response
    end
```

### DSDL datatypes

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

