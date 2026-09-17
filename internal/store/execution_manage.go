package store

import (
	"errors"
	"fmt"
)

// ErrExecutionActive is returned when an operation that requires a terminal
// execution is attempted on a pending/running row.
var ErrExecutionActive = errors.New("store: execution is active")

// ExecutionTerminal reports whether status is a finished execution state.
func ExecutionTerminal(status string) bool {
	return status != StatusPending && status != StatusRunning
}

// DeleteExecution removes one finished execution and returns the row that was
// removed. Pending/running rows are deliberately protected: callers must
// cancel them and wait for the terminal state first.
func (s *Store) DeleteExecution(id int64) (Execution, error) {
	e, err := s.GetExecution(id)
	if err != nil {
		return Execution{}, err
	}
	if !ExecutionTerminal(e.Status) {
		return Execution{}, fmt.Errorf("%w: execution %d has status %q", ErrExecutionActive, id, e.Status)
	}
	res, err := s.db.Exec(`DELETE FROM executions WHERE id=? AND status NOT IN (?, ?)`, id, StatusPending, StatusRunning)
	if err != nil {
		return Execution{}, err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		// A terminal row is never moved back to active by the scheduler. If no
		// row was removed, distinguish an unexpected active transition from a
		// concurrent/manual deletion so the API can return the right class of
		// error instead of silently succeeding.
		cur, getErr := s.GetExecution(id)
		if errors.Is(getErr, ErrNotFound) {
			return Execution{}, ErrNotFound
		}
		if getErr != nil {
			return Execution{}, getErr
		}
		return Execution{}, fmt.Errorf("%w: execution %d has status %q", ErrExecutionActive, id, cur.Status)
	}
	return e, nil
}
