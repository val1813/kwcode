package t60_go_raft_log

// Snapshot represents a point-in-time state machine snapshot.
type Snapshot struct {
	LastIncludedIndex int
	LastIncludedTerm  int
	Data              []byte
}

// SnapshotManager installs snapshots and tracks commit state.
type SnapshotManager struct {
	log         *RaftLog
	commitIndex int
	lastApplied int
	applied     []Entry // entries applied to state machine (for testing)
}

// NewSnapshotManager creates a SnapshotManager wrapping the given log.
func NewSnapshotManager(log *RaftLog) *SnapshotManager {
	return &SnapshotManager{log: log}
}

// InstallSnapshot applies a snapshot received from the leader.
// Bug: after installing the snapshot, commitIndex is not updated to
// max(commitIndex, snap.LastIncludedIndex). On the next ApplyEntries call,
// entries before snap.LastIncludedIndex are re-applied because commitIndex
// still points to the old value.
func (sm *SnapshotManager) InstallSnapshot(snap Snapshot) {
	sm.log.Compact(snap.LastIncludedIndex)
	// BUG: should update commitIndex:
	// if snap.LastIncludedIndex > sm.commitIndex {
	//     sm.commitIndex = snap.LastIncludedIndex
	// }
	// if snap.LastIncludedIndex > sm.lastApplied {
	//     sm.lastApplied = snap.LastIncludedIndex
	// }
	// Without this, commitIndex stays at its old value (possibly 0).
}

// UpdateCommit advances commitIndex to newCommit if it is higher.
func (sm *SnapshotManager) UpdateCommit(newCommit int) {
	if newCommit > sm.commitIndex {
		sm.commitIndex = newCommit
	}
}

// ApplyEntries applies all committed-but-not-yet-applied entries to the state machine.
func (sm *SnapshotManager) ApplyEntries() []Entry {
	var newly []Entry
	for sm.lastApplied < sm.commitIndex {
		sm.lastApplied++
		e, err := sm.log.At(sm.lastApplied)
		if err != nil {
			break
		}
		sm.applied = append(sm.applied, e)
		newly = append(newly, e)
	}
	return newly
}

// CommitIndex returns the current commitIndex.
func (sm *SnapshotManager) CommitIndex() int {
	return sm.commitIndex
}

// LastApplied returns the index of the last applied entry.
func (sm *SnapshotManager) LastApplied() int {
	return sm.lastApplied
}

// Applied returns all entries that have been applied (for testing).
func (sm *SnapshotManager) Applied() []Entry {
	return sm.applied
}
