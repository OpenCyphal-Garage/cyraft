# Unit tests Raft Node

## init

- `_unittest_raft_node_init`

- [x] All nodes start as follower

## Timers

### Term timeout

- `_unittest_raft_node_term_timeout`

- [x] Node term increases every term timeout

### Election timeout

- `_unittest_raft_node_election_timeout`
- `_unittest_raft_node_election_timeout_heartbeat`

- [x] Node converts to candidate after election timeout
- [x] Node doesn't convert to condidate if heartbeat received
  - [x] self.last_message_timestap is updated

## RPC

### RequestVote

- `_unittest_raft_node_request_vote_rpc`

- [x] Reply false if term < currentTerm
- [x] If votedFor is null or candidateId, and candidate's log is at least as up-to-date as receiver's log, grant vote

### AppendEntries

- `_unittest_raft_node_append_entries_rpc`

- [x] Reply false if term < currentTerm
- [x] Reply false if log doesn't contain an entry at prevLogIndex whose term matches prevLogTerm
- [x] If an existing entry conflicts with a new one (same index but different terms), delete the existing entry and all that follow it
- [x] Append any new entries not already in the log
- [ ] If leaderCommit > commitIndex, set commitIndex = min(leaderCommit, index of last new entry)