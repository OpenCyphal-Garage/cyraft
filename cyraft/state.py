class RaftState:
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"

    def __init__(self) -> None:
        self.state = RaftState.FOLLOWER
