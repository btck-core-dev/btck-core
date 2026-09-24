package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

type BenchmarkStats struct {
	TotalConnections int64
	SuccessfulAuths  int64
	JobsReceived     int64
	SharesSubmitted  int64
	SharesAccepted   int64
	Errors           int64
	TotalLatencyMs   int64
}

func main() {
	target := flag.String("target", "127.0.0.1:3334", "Stratum gateway address")
	concurrency := flag.Int("concurrency", 500, "Number of concurrent simulated miners")
	sharesPerMiner := flag.Int("shares", 5, "Number of shares submitted per miner")
	flag.Parse()

	fmt.Println("================================================================")
	fmt.Printf("🚀 BTC-K High-Concurrency Stratum Gateway Stress Test\n")
	fmt.Printf("   Target:           %s\n", *target)
	fmt.Printf("   Miners:           %d concurrent clients\n", *concurrency)
	fmt.Printf("   Shares / Miner:   %d\n", *sharesPerMiner)
	fmt.Println("================================================================")

	stats := &BenchmarkStats{}
	startTime := time.Now()

	var wg sync.WaitGroup
	semaphore := make(chan struct{}, 150) // rate limiter for dialing

	for i := 0; i < *concurrency; i++ {
		wg.Add(1)
		minerID := i
		semaphore <- struct{}{}
		go func(id int) {
			defer wg.Done()
			defer func() { <-semaphore }()
			runSimulatedMiner(*target, id, *sharesPerMiner, stats)
		}(minerID)
	}

	wg.Wait()
	duration := time.Since(startTime)

	avgLatency := float64(0)
	if stats.SharesAccepted > 0 {
		avgLatency = float64(stats.TotalLatencyMs) / float64(stats.SharesAccepted)
	}

	sharesPerSec := float64(stats.SharesAccepted) / duration.Seconds()

	fmt.Println("\n================================================================")
	fmt.Println("📊 Stress Test Results Benchmark Report:")
	fmt.Printf("   Elapsed Time:         %.2fs\n", duration.Seconds())
	fmt.Printf("   Total Connected:      %d / %d (%.1f%%)\n", stats.TotalConnections, *concurrency, float64(stats.TotalConnections)/float64(*concurrency)*100)
	fmt.Printf("   Authorized Miners:    %d / %d (%.1f%%)\n", stats.SuccessfulAuths, *concurrency, float64(stats.SuccessfulAuths)/float64(*concurrency)*100)
	fmt.Printf("   Mining Jobs Received: %d\n", stats.JobsReceived)
	fmt.Printf("   Shares Submitted:     %d\n", stats.SharesSubmitted)
	fmt.Printf("   Shares Accepted:      %d / %d (%.2f%% success)\n", stats.SharesAccepted, stats.SharesSubmitted, float64(stats.SharesAccepted)/float64(stats.SharesSubmitted)*100)
	fmt.Printf("   Connection Errors:    %d\n", stats.Errors)
	fmt.Printf("   Avg Share Latency:    %.2f ms\n", avgLatency)
	fmt.Printf("   Throughput:           %.1f shares/second\n", sharesPerSec)
	fmt.Println("================================================================")
}

func runSimulatedMiner(target string, id int, sharesCount int, stats *BenchmarkStats) {
	conn, err := net.DialTimeout("tcp", target, 3*time.Second)
	if err != nil {
		atomic.AddInt64(&stats.Errors, 1)
		return
	}
	defer conn.Close()
	atomic.AddInt64(&stats.TotalConnections, 1)

	reader := bufio.NewReader(conn)
	writer := bufio.NewWriter(conn)

	// Map for pending request callbacks
	pending := sync.Map{}
	doneChan := make(chan struct{})

	// Background reader goroutine to demultiplex async server notifies from RPC responses
	go func() {
		defer close(doneChan)
		for {
			line, err := reader.ReadBytes('\n')
			if err != nil {
				return
			}
			var msg map[string]interface{}
			if err := json.Unmarshal(line, &msg); err != nil {
				continue
			}

			// Async notifications
			if method, ok := msg["method"].(string); ok {
				if method == "mining.notify" {
					atomic.AddInt64(&stats.JobsReceived, 1)
				}
				continue
			}

			// RPC Responses
			if idVal, ok := msg["id"]; ok && idVal != nil {
				reqID := int64(idVal.(float64))
				if chVal, ok := pending.LoadAndDelete(reqID); ok {
					ch := chVal.(chan bool)
					isOk := false
					if res, ok := msg["result"].(bool); ok && res {
						isOk = true
					}
					select {
					case ch <- isOk:
					default:
					}
				}
			}
		}
	}()

	// 1. Subscribe
	subID := int64(id*10000 + 1)
	subCh := make(chan bool, 1)
	pending.Store(subID, subCh)
	_, _ = writer.WriteString(fmt.Sprintf(`{"id":%d,"method":"mining.subscribe","params":["IceRiverStressTester/1.0"]}`+"\n", subID))
	_ = writer.Flush()

	select {
	case <-subCh:
	case <-time.After(2 * time.Second):
		atomic.AddInt64(&stats.Errors, 1)
		return
	}

	// 2. Authorize
	authID := int64(id*10000 + 2)
	authCh := make(chan bool, 1)
	pending.Store(authID, authCh)
	workerName := fmt.Sprintf("ks0_ultra_%04d", id)
	walletAddr := fmt.Sprintf("btck1s_miner_%04d_test_address", id)
	_, _ = writer.WriteString(fmt.Sprintf(`{"id":%d,"method":"mining.authorize","params":["%s.%s",""]}`+"\n", authID, walletAddr, workerName))
	_ = writer.Flush()

	select {
	case ok := <-authCh:
		if ok {
			atomic.AddInt64(&stats.SuccessfulAuths, 1)
		}
	case <-time.After(2 * time.Second):
		atomic.AddInt64(&stats.Errors, 1)
		return
	}

	// 3. Submit Shares
	for s := 0; s < sharesCount; s++ {
		submitID := int64(id*10000 + 10 + s)
		submitCh := make(chan bool, 1)
		pending.Store(submitID, submitCh)

		nonce := fmt.Sprintf("%08x", id*1000+s)
		t0 := time.Now()
		_, _ = writer.WriteString(fmt.Sprintf(`{"id":%d,"method":"mining.submit","params":["%s.%s","1","%s"]}`+"\n", submitID, walletAddr, workerName, nonce))
		_ = writer.Flush()
		atomic.AddInt64(&stats.SharesSubmitted, 1)

		select {
		case accepted := <-submitCh:
			rtt := time.Since(t0).Milliseconds()
			atomic.AddInt64(&stats.TotalLatencyMs, rtt)
			if accepted {
				atomic.AddInt64(&stats.SharesAccepted, 1)
			}
		case <-time.After(2 * time.Second):
			atomic.AddInt64(&stats.Errors, 1)
		}

		time.Sleep(10 * time.Millisecond)
	}
}
