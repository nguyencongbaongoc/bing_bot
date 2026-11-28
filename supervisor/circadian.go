package main

import "time"

const (
	CircadianStartHour   = 7
	CircadianStartMinute = 0
	CircadianEndHour     = 22
	CircadianEndMinute   = 30
)

// IsWithinCircadianWindow returns true when local time is between 07:00 and 22:30 inclusive.
func IsWithinCircadianWindow(now time.Time) bool {
	mins := now.Hour()*60 + now.Minute()
	startMins := CircadianStartHour*60 + CircadianStartMinute
	endMins := CircadianEndHour*60 + CircadianEndMinute
	return mins >= startMins && mins <= endMins
}
