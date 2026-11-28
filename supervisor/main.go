package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type batchPlanSummary struct {
	HasBrowserTasks bool   `json:"has_browser_tasks"`
	RunActivities   bool   `json:"run_activities"`
	RunVisualSearch bool   `json:"run_visual_search"`
	PCQueries       []string `json:"pc_queries"`
	MobileQueries   []string `json:"mobile_queries"`
	KnownPoints     int    `json:"known_points_before"`
	KnownBingStar   int    `json:"known_bing_star_before"`
}

func resolveRootDir() string {
	exe, err := os.Executable()
	if err == nil {
		exeDir := filepath.Dir(exe)
		if _, statErr := os.Stat(filepath.Join(exeDir, "core", "main.py")); statErr == nil {
			return exeDir
		}
	}
	wd, err := os.Getwd()
	if err == nil {
		return wd
	}
	return "."
}

func resolvePythonExe(rootDir string) string {
	venvPy := filepath.Join(rootDir, ".venv", "Scripts", "python.exe")
	if _, err := os.Stat(venvPy); err == nil {
		return venvPy
	}
	if p, err := exec.LookPath("python"); err == nil {
		return p
	}
	return "python"
}

func resolveNodeExe() (string, error) {
	return exec.LookPath("node")
}

func checkNetwork(hostPort string, timeout time.Duration) bool {
	conn, err := net.DialTimeout("tcp", hostPort, timeout)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func writeTimeoutWorkerResult(rootDir, account string, plan batchPlanSummary) {
	resultPath := filepath.Join(rootDir, "data", "worker_result.json")
	if raw, err := os.ReadFile(resultPath); err == nil && len(raw) > 0 {
		var existing map[string]any
		if json.Unmarshal(raw, &existing) == nil {
			existing["status"] = "timeout"
			existing["finished_at"] = time.Now().Format(time.RFC3339)
			if errs, ok := existing["errors"].([]any); ok {
				existing["errors"] = append(errs, "Worker exceeded 220s timeout and was terminated by Go Job Object")
			} else {
				existing["errors"] = []string{"Worker exceeded 220s timeout and was terminated by Go Job Object"}
			}
			if b, mErr := json.MarshalIndent(existing, "", "  "); mErr == nil {
				_ = os.WriteFile(resultPath, b, 0644)
				return
			}
		}
	}

	fallback := map[string]any{
		"schema_version":          "1.0",
		"finished_at":             time.Now().Format(time.RFC3339),
		"account":                 account,
		"status":                  "timeout",
		"activities_done":         false,
		"daily_set_done":          false,
		"keep_earning_done":       false,
		"edge_browsing_done":      false,
		"visual_search_done":      false,
		"visual_search_attempted": false,
		"pc_completed":            0,
		"mobile_completed":        0,
		"pc_queries_used":         []string{},
		"mobile_queries_used":     []string{},
		"pc_cur":                  0,
		"pc_max":                  90,
		"mobile_cur":              0,
		"mobile_max":              60,
		"points_before":           plan.KnownPoints,
		"points_after":            plan.KnownPoints,
		"bing_star_points":        plan.KnownBingStar,
		"cooldown_detected":       false,
		"attempted_offers_added":  []string{},
		"attempted_quest_tasks_added": []string{},
		"errors":                  []string{"Worker exceeded 220s timeout and was terminated by Go Job Object"},
	}
	if b, err := json.MarshalIndent(fallback, "", "  "); err == nil {
		_ = os.WriteFile(resultPath, b, 0644)
	}
}

func main() {
	account := flag.String("account", "default", "Account ID")
	forceHours := flag.Bool("force-hours", false, "Bypass Circadian Guard (07:00 - 22:30)")
	noExitOnCooldown := flag.Bool("no-exit-on-cooldown", false, "Disable Stateless Burst cooldown exit")
	skipJitter := flag.Bool("skip-jitter", false, "Skip startup jitter (15-30s)")
	dryRun := flag.Bool("dry-run", false, "Run Tier-2 planner only and skip Tier-3 browser worker")
	flag.Parse()

	cleanAccount := strings.TrimSpace(*account)
	if cleanAccount == "" {
		cleanAccount = "default"
	}

	rootDir := resolveRootDir()
	logsDir := filepath.Join(rootDir, "logs")
	_ = os.MkdirAll(logsDir, 0755)
	logPath := filepath.Join(logsDir, fmt.Sprintf("supervisor_%s.log", time.Now().Format("2006-01-02")))

	var outWriter io.Writer = os.Stdout
	if logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644); err == nil {
		defer logFile.Close()
		outWriter = io.MultiWriter(os.Stdout, logFile)
	}

	logf := func(format string, args ...any) {
		ts := time.Now().Format("2006-01-02 15:04:05")
		msg := fmt.Sprintf(format, args...)
		_, _ = fmt.Fprintf(outWriter, "[%s] [SUPERVISOR] %s\n", ts, msg)
	}

	logf("=== MS Rewards Polyglot Supervisor starting (account=%s) ===", cleanAccount)

	// 1. Circadian Guard (07:00 - 22:30)
	if !*forceHours && !IsWithinCircadianWindow(time.Now()) {
		logf("[Circadian Guard] Outside active hours (07:00 - 22:30). Exiting with code 10.")
		os.Exit(10)
	}

	// 2. Windows Named Mutex (replaces PID lockfile permanently)
	mtx, acquired, mtxErr := AcquireAccountMutex(cleanAccount)
	if mtxErr != nil {
		logf("[Mutex] Failed to acquire Windows Named Mutex: %v (exit 1)", mtxErr)
		os.Exit(1)
	}
	if !acquired {
		logf("[Mutex] Another supervisor instance is already running for account '%s'. Exiting with code 11.", cleanAccount)
		os.Exit(11)
	}
	defer mtx.Release()

	// 3. Startup Jitter (15-30s)
	if !*skipJitter {
		jitter := 15 + rand.Intn(16)
		logf("[Stealth] Startup jitter: waiting %ds...", jitter)
		time.Sleep(time.Duration(jitter) * time.Second)
	}

	// 4. Pre-run Orphan Guard (only cleans bot's own profile Edge processes)
	profileDir := filepath.Join(rootDir, "profiles", cleanAccount)
	CleanupOrphanBotEdges(profileDir, logf)

	// 5. Network Guard
	if !checkNetwork("www.bing.com:443", 5*time.Second) {
		logf("[Network Guard] Cannot reach www.bing.com:443 within 5s. Exiting with code 1.")
		os.Exit(1)
	}

	pythonExe := resolvePythonExe(rootDir)
	nodeExe, nodeErr := resolveNodeExe()
	if nodeErr != nil {
		logf("[Config] Cannot locate 'node' executable in PATH: %v (exit 1)", nodeErr)
		os.Exit(1)
	}

	ctx := context.Background()

	// Stage 1: Tier 2 Python API & Planner (120s hard timeout in Job Object)
	planArgs := []string{
		filepath.Join(rootDir, "core", "main.py"),
		"--phase", "api-and-plan",
		"--account", cleanAccount,
	}
	if *noExitOnCooldown {
		planArgs = append(planArgs, "--no-exit-on-cooldown")
	}

	logf("[Stage 1/3] Running Python API & Planner (timeout=120s)...")
	code, err := RunInJob(ctx, pythonExe, planArgs, rootDir, 120*time.Second, outWriter)
	if err != nil || code != 0 {
		logf("[Stage 1/3] Failed (code=%d, err=%v). Exiting.", code, err)
		if code == 30 {
			os.Exit(30)
		}
		os.Exit(1)
	}

	planPath := filepath.Join(rootDir, "data", "batch_plan.json")
	planBytes, err := os.ReadFile(planPath)
	if err != nil {
		logf("[Stage 1/3] Failed to read %s: %v (exit 1)", planPath, err)
		os.Exit(1)
	}
	var plan batchPlanSummary
	if err := json.Unmarshal(planBytes, &plan); err != nil {
		logf("[Stage 1/3] Invalid batch_plan.json: %v (exit 1)", err)
		os.Exit(1)
	}

	if !plan.HasBrowserTasks {
		logf("[Pipeline] has_browser_tasks=false -> all daily tasks & searches done. Skipping TS worker (exit 0).")
		os.Exit(0)
	}
	if *dryRun {
		logf("[Dry-Run] Stopping after batch_plan.json generation (pc=%d, mobile=%d, activities=%v, vs=%v). Exit 0.",
			len(plan.PCQueries), len(plan.MobileQueries), plan.RunActivities, plan.RunVisualSearch)
		os.Exit(0)
	}

	// Stage 2: Tier 3 TypeScript Browser Worker (220s hard timeout in Job Object)
	workerScript := filepath.Join(rootDir, "browser-worker", "dist", "index.js")
	logf("[Stage 2/3] Running TypeScript Browser Worker in Windows Job Object (timeout=220s)...")
	workerCode, workerErr := RunInJob(ctx, nodeExe, []string{workerScript}, rootDir, 220*time.Second, outWriter)
	workerTimedOut := workerCode == 30 || errIsTimeout(workerErr)
	if workerTimedOut {
		logf("[Stage 2/3] WATCHDOG: Worker exceeded 220s -> TerminateJobObject killed entire process tree (code 30).")
		writeTimeoutWorkerResult(rootDir, cleanAccount, plan)
	} else if workerErr != nil || workerCode != 0 {
		logf("[Stage 2/3] Worker exited with code=%d, err=%v", workerCode, workerErr)
	}

	// Stage 3: Tier 2 Python Ingester (30s hard timeout in Job Object)
	ingestArgs := []string{
		filepath.Join(rootDir, "core", "main.py"),
		"--phase", "ingest-result",
		"--account", cleanAccount,
	}
	if *noExitOnCooldown {
		ingestArgs = append(ingestArgs, "--no-exit-on-cooldown")
	}

	logf("[Stage 3/3] Running Python Result Ingester (timeout=30s)...")
	ingestCode, ingestErr := RunInJob(ctx, pythonExe, ingestArgs, rootDir, 30*time.Second, outWriter)
	if ingestErr != nil {
		logf("[Stage 3/3] Ingester error: %v", ingestErr)
	}

	if workerTimedOut {
		logf("=== Supervisor finished with exit code 30 (worker timeout) ===")
		os.Exit(30)
	}
	logf("=== Supervisor finished with exit code %d ===", ingestCode)
	os.Exit(ingestCode)
}

func errIsTimeout(err error) bool {
	return err != nil && err == ErrJobTimeout
}
