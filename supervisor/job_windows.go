//go:build windows

package main

import (
	"context"
	"errors"
	"io"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var ErrJobTimeout = errors.New("process exceeded hard timeout and job object was terminated")

func buildCommandLine(exePath string, args []string) string {
	parts := make([]string, 0, 1+len(args))
	parts = append(parts, syscall.EscapeArg(exePath))
	for _, a := range args {
		parts = append(parts, syscall.EscapeArg(a))
	}
	return strings.Join(parts, " ")
}

func createJobWithKillOnClose() (windows.Handle, error) {
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return 0, err
	}
	var info windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
	info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	_, err = windows.SetInformationJobObject(
		job,
		windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&info)),
		uint32(unsafe.Sizeof(info)),
	)
	if err != nil {
		_ = windows.CloseHandle(job)
		return 0, err
	}
	return job, nil
}

// RunInJob launches exePath suspended inside a Windows Job Object, resumes it,
// streams stdout/stderr to outWriter, and kills the entire process tree via
// TerminateJobObject if hardTimeout or ctx expires.
func RunInJob(
	ctx context.Context,
	exePath string,
	args []string,
	workDir string,
	hardTimeout time.Duration,
	outWriter io.Writer,
) (int, error) {
	job, err := createJobWithKillOnClose()
	if err != nil {
		return 1, err
	}
	defer func() {
		if job != 0 {
			_ = windows.CloseHandle(job)
		}
	}()

	var readPipe, writePipe windows.Handle
	sa := windows.SecurityAttributes{
		Length:        uint32(unsafe.Sizeof(windows.SecurityAttributes{})),
		InheritHandle: 1,
	}
	if err := windows.CreatePipe(&readPipe, &writePipe, &sa, 0); err != nil {
		return 1, err
	}
	// Ensure parent's read end is NOT inherited by child
	_ = windows.SetHandleInformation(readPipe, windows.HANDLE_FLAG_INHERIT, 0)

	cmdLinePtr, err := windows.UTF16PtrFromString(buildCommandLine(exePath, args))
	if err != nil {
		_ = windows.CloseHandle(readPipe)
		_ = windows.CloseHandle(writePipe)
		return 1, err
	}
	var workDirPtr *uint16
	if workDir != "" {
		workDirPtr, err = windows.UTF16PtrFromString(workDir)
		if err != nil {
			_ = windows.CloseHandle(readPipe)
			_ = windows.CloseHandle(writePipe)
			return 1, err
		}
	}

	var si windows.StartupInfo
	si.Cb = uint32(unsafe.Sizeof(si))
	si.Flags = windows.STARTF_USESTDHANDLES
	si.StdOutput = writePipe
	si.StdErr = writePipe

	var pi windows.ProcessInformation
	baseFlags := uint32(windows.CREATE_SUSPENDED | windows.CREATE_NO_WINDOW | windows.CREATE_UNICODE_ENVIRONMENT)
	err = windows.CreateProcess(nil, cmdLinePtr, nil, nil, true, baseFlags, nil, workDirPtr, &si, &pi)
	if err != nil {
		_ = windows.CloseHandle(readPipe)
		_ = windows.CloseHandle(writePipe)
		return 1, err
	}

	if assignErr := windows.AssignProcessToJobObject(job, pi.Process); assignErr != nil {
		// Retry once with CREATE_BREAKAWAY_FROM_JOB in case a parent job restricts assignment
		_ = windows.TerminateProcess(pi.Process, 1)
		_ = windows.CloseHandle(pi.Thread)
		_ = windows.CloseHandle(pi.Process)
		pi = windows.ProcessInformation{}
		retryFlags := baseFlags | windows.CREATE_BREAKAWAY_FROM_JOB
		if retryErr := windows.CreateProcess(nil, cmdLinePtr, nil, nil, true, retryFlags, nil, workDirPtr, &si, &pi); retryErr != nil {
			_ = windows.CloseHandle(readPipe)
			_ = windows.CloseHandle(writePipe)
			return 1, assignErr
		}
		if assignErr2 := windows.AssignProcessToJobObject(job, pi.Process); assignErr2 != nil {
			_ = windows.TerminateProcess(pi.Process, 1)
			_ = windows.CloseHandle(pi.Thread)
			_ = windows.CloseHandle(pi.Process)
			_ = windows.CloseHandle(readPipe)
			_ = windows.CloseHandle(writePipe)
			return 1, assignErr2
		}
	}

	// Close parent's write end so readPipe gets EOF once all processes in the job exit
	_ = windows.CloseHandle(writePipe)

	pipeDone := make(chan struct{})
	go func() {
		defer close(pipeDone)
		buf := make([]byte, 4096)
		for {
			var n uint32
			rErr := windows.ReadFile(readPipe, buf, &n, nil)
			if n > 0 && outWriter != nil {
				_, _ = outWriter.Write(buf[:n])
			}
			if rErr != nil || n == 0 {
				break
			}
		}
	}()

	if _, err := windows.ResumeThread(pi.Thread); err != nil {
		_ = windows.TerminateJobObject(job, 1)
		_ = windows.CloseHandle(pi.Thread)
		_ = windows.CloseHandle(pi.Process)
		_ = windows.CloseHandle(readPipe)
		return 1, err
	}
	_ = windows.CloseHandle(pi.Thread)

	procDone := make(chan uint32, 1)
	go func() {
		event, _ := windows.WaitForSingleObject(pi.Process, windows.INFINITE)
		procDone <- event
	}()

	timer := time.NewTimer(hardTimeout)
	defer timer.Stop()

	timedOut := false
	select {
	case <-procDone:
	case <-timer.C:
		timedOut = true
	case <-ctx.Done():
		timedOut = true
	}

	if timedOut {
		_ = windows.TerminateJobObject(job, 30)
		_, _ = windows.WaitForSingleObject(pi.Process, 2000)
		_ = windows.CloseHandle(pi.Process)
		_ = windows.CloseHandle(readPipe)
		select {
		case <-pipeDone:
		case <-time.After(500 * time.Millisecond):
		}
		return 30, ErrJobTimeout
	}

	var exitCode uint32
	_ = windows.GetExitCodeProcess(pi.Process, &exitCode)
	_ = windows.CloseHandle(pi.Process)
	_ = windows.CloseHandle(readPipe)
	select {
	case <-pipeDone:
	case <-time.After(500 * time.Millisecond):
	}
	return int(exitCode), nil
}
