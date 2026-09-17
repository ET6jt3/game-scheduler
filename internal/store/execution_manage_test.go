package store

import (
	"errors"
	"testing"
)

func TestDeleteExecutionTerminalOnly(t *testing.T) {
	s := newTestStore(t)
	mkGame(t, s, "g")
	task, err := s.CreateTask(Task{GameID: "g", Name: "t", Type: "raw", Enabled: true})
	if err != nil { t.Fatal(err) }

	for _, status := range []string{StatusFailed, StatusCancelled, StatusSuccess} {
		e, err := s.CreateExecution(Execution{TaskID: task.ID, Trigger: TriggerManual, Status: status})
		if err != nil { t.Fatal(err) }
		deleted, err := s.DeleteExecution(e.ID)
		if err != nil { t.Fatalf("delete %s: %v", status, err) }
		if deleted.Status != status { t.Fatalf("deleted status=%q want %q", deleted.Status, status) }
		if _, err := s.GetExecution(e.ID); !errors.Is(err, ErrNotFound) { t.Fatalf("row still exists: %v", err) }
	}
}

func TestDeleteExecutionRejectsActive(t *testing.T) {
	s := newTestStore(t)
	mkGame(t, s, "g")
	task, err := s.CreateTask(Task{GameID: "g", Name: "t", Type: "raw", Enabled: true})
	if err != nil { t.Fatal(err) }

	for _, status := range []string{StatusPending, StatusRunning} {
		e, err := s.CreateExecution(Execution{TaskID: task.ID, Trigger: TriggerManual, Status: status})
		if err != nil { t.Fatal(err) }
		if _, err := s.DeleteExecution(e.ID); !errors.Is(err, ErrExecutionActive) {
			t.Fatalf("delete %s err=%v, want ErrExecutionActive", status, err)
		}
		if _, err := s.GetExecution(e.ID); err != nil { t.Fatalf("active row was removed: %v", err) }
	}
}
