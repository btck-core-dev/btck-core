package main

import (
	"math"
	"sync"
)

type VardiffEngine struct {
	mu           sync.Mutex
	targetSec    float64
	minDiff      float64
	maxDiff      float64
	windowSize   int
	damping      float64
	minDeltaStep float64
}

func NewVardiffEngine(cfg *Config) *VardiffEngine {
	return &VardiffEngine{
		targetSec:    cfg.VardiffTargetSec,
		minDiff:      cfg.MinDiff,
		maxDiff:      cfg.MaxDiff,
		windowSize:   cfg.VardiffWindow,
		damping:      0.35,
		minDeltaStep: 0.2,
	}
}

// EvaluateRetarget calculates whether client difficulty should be updated
func (v *VardiffEngine) EvaluateRetarget(intervals []float64, currentDiff float64) (bool, float64) {
	v.mu.Lock()
	defer v.mu.Unlock()

	if len(intervals) < v.windowSize {
		return false, currentDiff
	}

	var sum float64
	for _, sec := range intervals {
		sum += sec
	}
	avg := sum / float64(len(intervals))

	// Deadband: within 0.90s to 1.10s of target, keep locked
	if avg >= 0.90*v.targetSec && avg <= 1.10*v.targetSec {
		return false, currentDiff
	}

	clampedAvg := math.Max(0.2, math.Min(5.0, avg))
	ratio := v.targetSec / clampedAvg
	newDiff := currentDiff * (1.0 + v.damping*(ratio-1.0))
	newDiff = math.Max(v.minDiff, math.Min(v.maxDiff, newDiff))

	// Round to 1 decimal place
	newDiff = math.Round(newDiff*10) / 10

	if math.Abs(newDiff-currentDiff) >= v.minDeltaStep {
		return true, newDiff
	}

	return false, currentDiff
}
