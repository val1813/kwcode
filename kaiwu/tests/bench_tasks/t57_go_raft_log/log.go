package t60_go_raft_log

import "fmt"

// RaftLog stores log entries and tracks compaction state.
type RaftLog struct {
	entries          []Entry // entries[0] is a dummy sentinel
	lastIncludedIdx  int     // index of last entry included in snapshot
	lastIncludedTerm int
}

// NewRaftLog creates an empty log with a sentinel entry at index 0.
func NewRaftLog() *RaftLog {
	return &RaftLog{
		entries: []Entry{{Index: 0, Term: 0}},
	}
}

// LastIndex returns the index of the last log entry.
func (l *RaftLog) LastIndex() int {
	return l.entries[len(l.entries)-1].Index
}

// LastTerm returns the term of the last log entry.
func (l *RaftLog) LastTerm() int {
	return l.entries[len(l.entries)-1].Term
}

// Append adds entries to the log.
func (l *RaftLog) Append(entries ...Entry) {
	l.entries = append(l.entries, entries...)
}

// At returns the entry at the given absolute index.
func (l *RaftLog) At(index int) (Entry, error) {
	offset := l.lastIncludedIdx
	i := index - offset
	if i < 0 || i >= len(l.entries) {
		return Entry{}, fmt.Errorf("index %d out of range (offset=%d, len=%d)", index, offset, len(l.entries))
	}
	return l.entries[i], nil
}

// Slice returns entries in [lo, hi).
func (l *RaftLog) Slice(lo, hi int) []Entry {
	offset := l.lastIncludedIdx
	return l.entries[lo-offset : hi-offset]
}

// TruncateAfter removes all entries after (and including) the given index.
func (l *RaftLog) TruncateAfter(index int) {
	offset := l.lastIncludedIdx
	i := index - offset
	if i >= 0 && i < len(l.entries) {
		l.entries = l.entries[:i]
	}
}

// Compact discards entries up to and including compactUpTo.
// Bug: sets lastIncludedIdx to len(entries)-1 (a relative position) instead
// of compactUpTo (the absolute index). When there are holes this is wrong.
func (l *RaftLog) Compact(compactUpTo int) error {
	offset := l.lastIncludedIdx
	i := compactUpTo - offset
	if i < 0 || i >= len(l.entries) {
		return fmt.Errorf("cannot compact to %d: out of range", compactUpTo)
	}
	term := l.entries[i].Term
	l.entries = l.entries[i:] // keep entries from compactUpTo onward
	l.entries[0] = Entry{Index: compactUpTo, Term: term} // sentinel

	// BUG: should be l.lastIncludedIdx = compactUpTo
	l.lastIncludedIdx = len(l.entries) - 1 // wrong: uses relative length
	l.lastIncludedTerm = term
	return nil
}

// LastIncludedIndex returns the index of the last compacted entry.
func (l *RaftLog) LastIncludedIndex() int {
	return l.lastIncludedIdx
}

// LastIncludedTerm returns the term of the last compacted entry.
func (l *RaftLog) LastIncludedTerm() int {
	return l.lastIncludedTerm
}
