package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type StratumServer struct {
	cfg        *Config
	bus        *RedisBus
	vardiff    *VardiffEngine
	clients    sync.Map
	currentJob *MiningJob
	jobMu      sync.RWMutex
	jobCounter uint64
	msgCounter uint64
	listener   net.Listener
	running    atomic.Bool
}

func NewStratumServer(cfg *Config, bus *RedisBus) *StratumServer {
	return &StratumServer{
		cfg:     cfg,
		bus:     bus,
		vardiff: NewVardiffEngine(cfg),
	}
}

func (s *StratumServer) Start() error {
	l, err := net.Listen("tcp", s.cfg.ListenPort)
	if err != nil {
		return fmt.Errorf("failed to bind port %s: %w", s.cfg.ListenPort, err)
	}
	s.listener = l
	s.running.Store(true)

	// Create initial template
	s.createNewJob(true)

	// Listen for remote jobs from Redis
	s.bus.StartJobListener(func(job *MiningJob) {
		s.jobMu.Lock()
		s.currentJob = job
		s.jobMu.Unlock()
		s.broadcastJob(job)
	})

	log.Printf("==============================================================")
	log.Printf("🚀 BTC-K High-Concurrency Go-Stratum Gateway Started")
	log.Printf("   Listening on:   %s", s.cfg.ListenPort)
	log.Printf("   Default Diff:   %.1f (Target: 1.0s/block)", s.cfg.DefaultDiff)
	log.Printf("   IceRiver 82-char format: ENABLED")
	log.Printf("   Redis Pub/Sub:  %s", s.cfg.RedisAddr)
	log.Printf("==============================================================")

	for s.running.Load() {
		conn, err := l.Accept()
		if err != nil {
			if !s.running.Load() {
				break
			}
			continue
		}
		go s.handleConnection(conn)
	}
	return nil
}

func (s *StratumServer) handleConnection(conn net.Conn) {
	client := NewStratumClient(conn, s.cfg.DefaultDiff)
	s.clients.Store(client.clientID, client)
	defer func() {
		s.clients.Delete(client.clientID)
		client.Close()
		log.Printf("🛑 ASIC Miner disconnected: %s (Worker: %s, Accepted: %d)", client.clientID, client.workerName, client.acceptedShares)
	}()

	scanner := bufio.NewScanner(client.reader)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var req StratumRequest
		if err := json.Unmarshal(line, &req); err != nil {
			continue
		}
		if err := s.processMessage(client, &req); err != nil {
			break
		}
	}
}

func (s *StratumServer) processMessage(c *StratumClient, req *StratumRequest) error {
	switch req.Method {
	case "mining.subscribe":
		agent := "IceRiverMiner-v1.1"
		if len(req.Params) > 0 {
			if a, ok := req.Params[0].(string); ok {
				agent = a
			}
		}
		log.Printf("✓ Subscribed miner [%s]: %s", c.clientID, agent)
		resp := StratumResponse{
			ID:     req.ID,
			Result: []interface{}{true, agent},
			Error:  nil,
		}
		if err := c.SendJSON(resp); err != nil {
			return err
		}

		// Send initial difficulty & extranonce
		_ = c.SetDifficulty(c.difficulty)
		_ = c.SendJSON(StratumNotification{
			ID:     atomic.AddUint64(&s.msgCounter, 1),
			Method: "mining.set_extranonce",
			Params: []interface{}{c.extraNonce1[:4]},
		})

	case "mining.authorize":
		user := "anonymous.default"
		if len(req.Params) > 0 {
			if u, ok := req.Params[0].(string); ok {
				user = u
			}
		}
		parts := strings.SplitN(user, ".", 2)
		rawAddr := parts[0]
		if strings.HasPrefix(rawAddr, "btck1s") {
			c.walletAddr = rawAddr
		} else {
			c.walletAddr = s.cfg.PoolStealthAddr
		}
		if len(parts) > 1 {
			c.workerName = parts[1]
		} else {
			c.workerName = "ks0_ultra"
		}
		c.authorized = true
		log.Printf("✓ Authorized miner [%s]: Worker='%s', PayoutAddr='%s...'", c.clientID, c.workerName, c.walletAddr[:24])

		_ = c.SendJSON(StratumResponse{
			ID:     req.ID,
			Result: true,
			Error:  nil,
		})

		// Send latest job
		s.jobMu.RLock()
		job := s.currentJob
		s.jobMu.RUnlock()
		if job != nil {
			s.sendJobToClient(c, job)
		}

	case "mining.submit":
		jobID := ""
		nonce := ""
		if len(req.Params) >= 3 {
			jobID = fmt.Sprintf("%v", req.Params[1])
			nonce = fmt.Sprintf("%v", req.Params[2])
		}

		interval := c.RecordShare()
		_ = c.SendJSON(StratumResponse{
			ID:     req.ID,
			Result: true,
			Error:  nil,
		})

		// Publish share to Redis pipeline
		s.bus.PublishShare(&SharePayload{
			MinerAddr:  c.walletAddr,
			WorkerName: c.workerName,
			JobID:      jobID,
			Nonce:      nonce,
			Difficulty: c.difficulty,
			Timestamp:  time.Now(),
			IsBlock:    true,
		})

		// Fast Vardiff evaluation every 20 shares
		if c.sharesSinceCheck >= 20 {
			if shouldUpdate, newDiff := s.vardiff.EvaluateRetarget(c.intervals, c.difficulty); shouldUpdate {
				_ = c.SetDifficulty(newDiff)
			}
			c.sharesSinceCheck = 0
		}

		// Create and broadcast next 1-second block
		newJob := s.createNewJob(true)
		s.broadcastJob(newJob)
	}

	return nil
}

func (s *StratumServer) createNewJob(clean bool) *MiningJob {
	id := atomic.AddUint64(&s.jobCounter, 1)
	header := sha256.Sum256([]byte(fmt.Sprintf("BLOCK_JOB_%d_%d", id, time.Now().UnixNano())))

	job := &MiningJob{
		JobID:      fmt.Sprintf("%d", id),
		Height:     id,
		HeaderHash: header,
		TargetDiff: s.cfg.DefaultDiff,
		CleanJobs:  clean,
		CreatedAt:  time.Now(),
	}

	s.jobMu.Lock()
	s.currentJob = job
	s.jobMu.Unlock()
	return job
}

func (s *StratumServer) sendJobToClient(c *StratumClient, job *MiningJob) {
	if !c.authorized {
		return
	}
	payload := FormatIceRiverNotifyPayload(job.HeaderHash[:], job.CleanJobs)
	notif := StratumNotification{
		ID:     nil,
		Method: "mining.notify",
		Params: []interface{}{job.JobID, payload},
	}
	_ = c.SendJSON(notif)
}

func (s *StratumServer) broadcastJob(job *MiningJob) {
	s.clients.Range(func(key, value interface{}) bool {
		if client, ok := value.(*StratumClient); ok {
			s.sendJobToClient(client, job)
		}
		return true
	})
}

func (s *StratumServer) Stop() {
	s.running.Store(false)
	if s.listener != nil {
		_ = s.listener.Close()
	}
}
