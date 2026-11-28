//go:build windows

package main

import (
	"errors"
	"fmt"
	"strings"

	"golang.org/x/sys/windows"
)

type AccountMutex struct {
	handle windows.Handle
	name   string
}

// AcquireAccountMutex acquires a Windows kernel Named Mutex for the account.
// Returns (mutex, true, nil) on success, (nil, false, nil) if already held by another process.
func AcquireAccountMutex(account string) (*AccountMutex, bool, error) {
	clean := strings.TrimSpace(account)
	if clean == "" {
		clean = "default"
	}
	candidates := []string{
		fmt.Sprintf(`Global\MSRewardsBot_%s`, clean),
		fmt.Sprintf(`Local\MSRewardsBot_%s`, clean),
	}

	var lastErr error
	for _, name := range candidates {
		ptr, err := windows.UTF16PtrFromString(name)
		if err != nil {
			return nil, false, err
		}
		h, createErr := windows.CreateMutex(nil, false, ptr)
		if errors.Is(createErr, windows.ERROR_ALREADY_EXISTS) {
			if h != 0 {
				_ = windows.CloseHandle(h)
			}
			return nil, false, nil
		}
		if createErr == nil && h != 0 {
			return &AccountMutex{handle: h, name: name}, true, nil
		}
		lastErr = createErr
		if !errors.Is(createErr, windows.ERROR_ACCESS_DENIED) {
			break
		}
	}
	return nil, false, lastErr
}

// Release frees the kernel mutex handle.
func (m *AccountMutex) Release() {
	if m != nil && m.handle != 0 {
		_ = windows.CloseHandle(m.handle)
		m.handle = 0
	}
}
