package t60_go_raft_log

import (
	"testing"
)

// TestCompactUpdatesLastIncludedIndex verifies that after compaction,
// LastIncludedIndex equals the compacted-up-to index, not a relative offset.
func TestCompactUpdatesLastIncludedIndex(t *testing.T) {
	l := NewRaftLog()
	for i := 1; i <= 10; i++ {
		l.Append(Entry{Index: i, Term: 1, Command: i})
	}

	if err := l.Compact(5); err != nil {
		t.Fatalf("Compact(5) failed: %v", err)
	}

	if l.LastIncludedIndex() != 5 {
		t.Errorf("after Compact(5), LastIncludedIndex should be 5, got %d — off-by-one in compaction", l.LastIncludedIndex())
	}

	// Entries after compaction should still be accessible.
	e, err := l.At(7)
	if err != nil {
		t.Fatalf("At(7) after Compact(5) failed: %v", err)
	}
	if e.Index != 7 {
		t.Errorf("At(7) returned entry with index %d", e.Index)
	}
}

// TestCompactThenCompactAgain verifies chained compaction works correctly.
func TestCompactThenCompactAgain(t *testing.T) {
	l := NewRaftLog()
	for i := 1; i <= 20; i++ {
		l.Append(Entry{Index: i, Term: 1})
	}

	if err := l.Compact(5); err != nil {
		t.Fatalf("first Compact(5): %v", err)
	}
	if l.LastIncludedIndex() != 5 {
		t.Errorf("after first compact: want LastIncludedIndex=5, got %d", l.LastIncludedIndex())
	}

	if err := l.Compact(10); err != nil {
		t.Fatalf("second Compact(10): %v", err)
	}
	if l.LastIncludedIndex() != 10 {
		t.Errorf("after second compact: want LastIncludedIndex=10, got %d — chained compaction broken", l.LastIncludedIndex())
	}
}

// TestSnapshotInstallUpdatesCommitIndex verifies that InstallSnapshot advances
// commitIndex so that already-snapshotted entries are not re-applied.
func TestSnapshotInstallUpdatesCommitIndex(t *testing.T) {
	l := NewRaftLog()
	for i := 1; i <= 10; i++ {
		l.Append(Entry{Index: i, Term: 1, Command: i})
	}
	sm := NewSnapshotManager(l)

	snap := Snapshot{LastIncludedIndex: 5, LastIncludedTerm: 1}
	sm.InstallSnapshot(snap)

	if sm.CommitIndex() < 5 {
		t.Errorf("after InstallSnapshot(5), commitIndex should be >= 5, got %d — snapshot does not update commitIndex", sm.CommitIndex())
	}

	// Now update commit to 8 and apply — should only apply entries 6..8.
	sm.UpdateCommit(8)
	applied := sm.ApplyEntries()

	for _, e := range applied {
		if e.Index <= 5 {
			t.Errorf("entry %d was re-applied after snapshot at index 5 — commitIndex not updated on snapshot install", e.Index)
		}
	}
	if len(applied) != 3 {
		t.Errorf("expected 3 newly applied entries (6,7,8), got %d", len(applied))
	}
}

// TestReplicatorConflictJumpsToHint verifies that on a conflict reply the
// replicator uses ConflictIndex to jump directly rather than decrementing by 1.
func TestReplicatorConflictJumpsToHint(t *testing.T) {
	l := NewRaftLog()
	for i := 1; i <= 100; i++ {
		l.Append(Entry{Index: i, Term: 1})
	}

	r := NewReplicator(l, []int{1})
	initialNext := r.NextIndex(1) // should be 101

	args := r.BuildArgs(1, 1, 0)
	reply := AppendEntriesReply{
		Success:       false,
		ConflictIndex: 10, // follower diverges at index 10
		ConflictTerm:  1,
	}
	r.HandleReply(1, args, reply)

	// With the fix, nextIndex should jump to ConflictIndex (10).
	// With the bug, nextIndex decrements by 1 to 100.
	if r.NextIndex(1) == initialNext-1 {
		t.Errorf("nextIndex decremented by 1 (linear rollback) instead of jumping to ConflictIndex=%d — use hint-based rollback", reply.ConflictIndex)
	}
	if r.NextIndex(1) != reply.ConflictIndex {
		t.Errorf("expected nextIndex=%d after conflict, got %d", reply.ConflictIndex, r.NextIndex(1))
	}
}
