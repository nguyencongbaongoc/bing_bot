package main

import (
	"testing"
	"time"
)

func TestIsWithinCircadianWindow(t *testing.T) {
	loc := time.Local
	cases := []struct {
		hour int
		min  int
		want bool
	}{
		{0, 15, false},
		{6, 59, false},
		{7, 0, true},
		{12, 30, true},
		{22, 30, true},
		{22, 31, false},
		{23, 55, false},
	}

	for _, tc := range cases {
		tm := time.Date(2026, 11, 27, tc.hour, tc.min, 0, 0, loc)
		got := IsWithinCircadianWindow(tm)
		if got != tc.want {
			t.Errorf("IsWithinCircadianWindow(%02d:%02d) = %v, want %v", tc.hour, tc.min, got, tc.want)
		}
	}
}

func TestAcquireAccountMutex(t *testing.T) {
	testAccount := "unit_test_mutex_acc"

	m1, ok1, err1 := AcquireAccountMutex(testAccount)
	if err1 != nil {
		t.Fatalf("first AcquireAccountMutex returned error: %v", err1)
	}
	if !ok1 || m1 == nil {
		t.Fatalf("expected first mutex acquire to succeed")
	}

	// Second acquire while first is held must fail cleanly (equivalent to exit 11)
	m2, ok2, err2 := AcquireAccountMutex(testAccount)
	if err2 != nil {
		t.Fatalf("second AcquireAccountMutex returned unexpected error: %v", err2)
	}
	if ok2 || m2 != nil {
		if m2 != nil {
			m2.Release()
		}
		m1.Release()
		t.Fatalf("expected second acquire to fail while mutex is held")
	}

	// After Release(), acquiring again must succeed
	m1.Release()
	m3, ok3, err3 := AcquireAccountMutex(testAccount)
	if err3 != nil || !ok3 || m3 == nil {
		t.Fatalf("expected re-acquire after Release() to succeed, got ok=%v err=%v", ok3, err3)
	}
	m3.Release()
}
