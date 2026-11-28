//go:build windows

package main

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows"
)

type win32ProcEntry struct {
	ProcessId   uint32 `json:"ProcessId"`
	CommandLine string `json:"CommandLine"`
}

// CleanupOrphanBotEdges kills only msedge.exe processes whose CommandLine references
// the bot's own profiles/<account> directory, never touching the user's personal Edge.
func CleanupOrphanBotEdges(profileDir string, logf func(string, ...any)) int {
	cleanProfile := strings.ToLower(filepath.Clean(profileDir))
	if cleanProfile == "" || cleanProfile == "." {
		return 0
	}

	psScript := `Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" | Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress`
	cmd := exec.Command("powershell", "-NoProfile", "-Command", psScript)
	out, err := cmd.Output()
	if err != nil || len(strings.TrimSpace(string(out))) == 0 {
		return 0
	}

	raw := strings.TrimSpace(string(out))
	var entries []win32ProcEntry
	if strings.HasPrefix(raw, "[") {
		if err := json.Unmarshal([]byte(raw), &entries); err != nil {
			return 0
		}
	} else {
		var single win32ProcEntry
		if err := json.Unmarshal([]byte(raw), &single); err != nil {
			return 0
		}
		entries = append(entries, single)
	}

	killed := 0
	for _, p := range entries {
		if p.ProcessId == 0 || p.CommandLine == "" {
			continue
		}
		cmdLower := strings.ToLower(p.CommandLine)
		if !strings.Contains(cmdLower, cleanProfile) {
			continue
		}
		h, openErr := windows.OpenProcess(windows.PROCESS_TERMINATE, false, p.ProcessId)
		if openErr == nil && h != 0 {
			if termErr := windows.TerminateProcess(h, 1); termErr == nil {
				killed++
			}
			_ = windows.CloseHandle(h)
		}
	}
	if killed > 0 && logf != nil {
		logf("[Orphan Guard] Cleaned up %d lingering bot msedge.exe processes for %s", killed, profileDir)
	}
	return killed
}
