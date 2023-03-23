# Cyraft

This is an exercise in implemeting the Raft algorithm, as it could be useful within pycyphal, in order to implement "named topics". The reason why we're interested in supporting "named topics": this could (eventually) be leveraged to allow Cyphal to function as a communication layer between PX4 and ROS. (See [UAVCAN as a middleware for ROS](https://forum.opencyphal.org/t/an-exploratory-study-uavcan-as-a-middleware-for-ros/872))

## TODO

- [x] *log*
  - [x] logger
- [x] serializer
  - [x] MessagePackSerializer
  - [x] tests
- [x] *network*
  - [x] UDPTransport
    - [Documentation UDP Echo Server/Client](https://docs.python.org/3/library/asyncio-protocol.html#udp-echo-server)
  - [x] tests
- [x] *timer*
  - [x] Timer
  - [x] tests
- [ ] *storage*
  - [x] FileDict
  - [ ] Log
  - [ ] StageMachine
  - [ ] FileStorage
  - [ ] tests
- [ ] *state*
  - [ ] BaseState
    - [ ] Leader
    - [ ] Candidate
    - [ ] Follower
  - [ ] State: Abstraction layer between Server & Raft State and Storage/Log & Raft State
  - [ ] tests
- [ ] *server*
  - [ ] Node
  - [ ] tests

- [ ] Initially all communication is done through sockets using `UDPProtocol`, however eventually the goal is to replace this communication with an instance of `Transport` (`pycphal/transport`)
- [ ] `MessagePackSerializer` is packed into a `Transfer` instance in pycyphal.

- [ ] typing
- [ ] class structuring/
  - [ ] @property
  - [ ] @abc.abstractmethod
  - [ ] @staticmethod

## Architecture

## Sources

[Raft paper](https://raft.github.io/raft.pdf)

[lynix94/pyraft](https://github.com/lynix94/pyraft)

[zhebrak/raftos](https://github.com/zhebrak/raftos)

[An exploratory study: UAVCAN as a middleware for ROS](https://forum.opencyphal.org/t/an-exploratory-study-uavcan-as-a-middleware-for-ros/872)

[Allocators explanation in OpenCyphal/public_regulated_data_types](https://github.com/OpenCyphal/public_regulated_data_types/blob/master/uavcan/pnp/8165.NodeIDAllocationData.2.0.dsdl)

