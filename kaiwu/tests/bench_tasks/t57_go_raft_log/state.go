// Package t60_go_raft_log implements a simplified Raft log replication subsystem.
// Bugs:
// 1. log.go: during log compaction, lastIncludedIndex is computed as
//    len(entries)-1 instead of the actual index of the last compacted entry,
//    causing an off-by-one when there are holes in the log.
// 2. replicator.go: on AppendEntries conflict the nextIndex rollback uses
//    linear decrement (nextIndex--) instead of binary search, making recovery
//    O(n) per round-trip instead of O(log n) and causing incorrect convergence
//    when the follower's log has many conflicting entries.
// 3. snapshot.go: after installing a snapshot, commitIndex is not updated to
//    max(commitIndex, snapshot.LastIncludedIndex), so already-committed entries
//    before the snapshot point get re-applied on the next tick.
package t60_go_raft_log

// Entry is a single Raft log entry.
type Entry struct {
	Index   int
	Term    int
	Command interface{}
}

// AppendEntriesArgs is the RPC argument for AppendEntries.
type AppendEntriesArgs struct {
	LeaderTerm   int
	PrevLogIndex int
	PrevLogTerm  int
	Entries      []Entry
	LeaderCommit int
}

// AppendEntriesReply is the RPC reply for AppendEntries.
type AppendEntriesReply struct {
	Term          int
	Success       bool
	ConflictIndex int // hint for fast rollback
	ConflictTerm  int
}
