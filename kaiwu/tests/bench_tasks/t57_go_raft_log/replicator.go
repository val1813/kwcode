package t60_go_raft_log

// Replicator tracks per-follower replication state and drives AppendEntries.
type Replicator struct {
	log       *RaftLog
	nextIndex map[int]int // follower id → next index to send
	matchIndex map[int]int // follower id → highest matched index
}

// NewReplicator creates a Replicator for the given followers.
func NewReplicator(log *RaftLog, followerIDs []int) *Replicator {
	r := &Replicator{
		log:        log,
		nextIndex:  make(map[int]int),
		matchIndex: make(map[int]int),
	}
	for _, id := range followerIDs {
		r.nextIndex[id] = log.LastIndex() + 1
		r.matchIndex[id] = 0
	}
	return r
}

// BuildArgs constructs an AppendEntries RPC for the given follower.
func (r *Replicator) BuildArgs(followerID, leaderTerm, leaderCommit int) AppendEntriesArgs {
	next := r.nextIndex[followerID]
	prevIndex := next - 1
	var prevTerm int
	if e, err := r.log.At(prevIndex); err == nil {
		prevTerm = e.Term
	}
	var entries []Entry
	if next <= r.log.LastIndex() {
		entries = r.log.Slice(next, r.log.LastIndex()+1)
	}
	return AppendEntriesArgs{
		LeaderTerm:   leaderTerm,
		PrevLogIndex: prevIndex,
		PrevLogTerm:  prevTerm,
		Entries:      entries,
		LeaderCommit: leaderCommit,
	}
}

// HandleReply processes an AppendEntries reply and updates replication state.
// Bug: on conflict (Success=false), nextIndex is decremented by 1 (linear).
// The correct approach is to use reply.ConflictIndex as a binary-search hint
// so that convergence is O(log n). With linear rollback, a follower that is
// far behind requires O(n) round-trips to catch up, and if ConflictIndex
// points to a much earlier position the leader never jumps there.
func (r *Replicator) HandleReply(followerID int, args AppendEntriesArgs, reply AppendEntriesReply) {
	if reply.Success {
		newMatch := args.PrevLogIndex + len(args.Entries)
		if newMatch > r.matchIndex[followerID] {
			r.matchIndex[followerID] = newMatch
		}
		r.nextIndex[followerID] = r.matchIndex[followerID] + 1
		return
	}
	// BUG: should jump to reply.ConflictIndex (or use binary search).
	// Instead we decrement by 1, which is O(n) and ignores the hint.
	r.nextIndex[followerID]-- // BUG: linear rollback
	if r.nextIndex[followerID] < 1 {
		r.nextIndex[followerID] = 1
	}
}

// NextIndex returns the nextIndex for a follower.
func (r *Replicator) NextIndex(followerID int) int {
	return r.nextIndex[followerID]
}

// MatchIndex returns the matchIndex for a follower.
func (r *Replicator) MatchIndex(followerID int) int {
	return r.matchIndex[followerID]
}
