package main

import (
	"context"
	"encoding/json"
	"log"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

type SharePayload struct {
	MinerAddr  string    `json:"miner_addr"`
	WorkerName string    `json:"worker_name"`
	JobID      string    `json:"job_id"`
	Nonce      string    `json:"nonce"`
	Difficulty float64   `json:"difficulty"`
	Timestamp  time.Time `json:"timestamp"`
	IsBlock    bool      `json:"is_block"`
}

type RedisBus struct {
	client     *redis.Client
	ctx        context.Context
	cancel     context.CancelFunc
	connected  bool
	localJobs  chan *MiningJob
	subscribers map[string]chan *MiningJob
	subMu      sync.Mutex
}

func NewRedisBus(cfg *Config) *RedisBus {
	ctx, cancel := context.WithCancel(context.Background())
	rdb := redis.NewClient(&redis.Options{
		Addr:     cfg.RedisAddr,
		Password: cfg.RedisPassword,
		DB:       cfg.RedisDB,
		DialTimeout: 2 * time.Second,
	})

	bus := &RedisBus{
		client:      rdb,
		ctx:         ctx,
		cancel:      cancel,
		localJobs:   make(chan *MiningJob, 100),
		subscribers: make(map[string]chan *MiningJob),
	}

	// Probe connection
	_, err := rdb.Ping(ctx).Result()
	if err != nil {
		log.Printf("⚠️ [Redis Bus] Could not connect to Redis at %s: %v. Running in Standalone In-Memory Fallback Mode.", cfg.RedisAddr, err)
		bus.connected = false
	} else {
		log.Printf("✓ [Redis Bus] Connected to Redis cluster at %s", cfg.RedisAddr)
		bus.connected = true
	}

	return bus
}

// PublishShare pushes miner share to Redis stream for accounting & DAG ingestion
func (r *RedisBus) PublishShare(share *SharePayload) {
	if !r.connected {
		return
	}
	data, _ := json.Marshal(share)
	_ = r.client.XAdd(r.ctx, &redis.XAddArgs{
		Stream: "btck:shares:stream",
		Values: map[string]interface{}{
			"payload": string(data),
		},
	}).Err()

	// Update miner hashrate metric hash
	key := "btck:miner:" + share.MinerAddr
	r.client.HIncrBy(r.ctx, key, "shares_total", 1)
	r.client.HSet(r.ctx, key, "last_active", time.Now().Unix())
}

// StartJobListener listens for new blocks from the consensus node
func (r *RedisBus) StartJobListener(handler func(job *MiningJob)) {
	if !r.connected {
		go func() {
			for job := range r.localJobs {
				handler(job)
			}
		}()
		return
	}

	pubsub := r.client.Subscribe(r.ctx, "btck:jobs:broadcast")
	go func() {
		ch := pubsub.Channel()
		for msg := range ch {
			var job MiningJob
			if err := json.Unmarshal([]byte(msg.Payload), &job); err == nil {
				handler(&job)
			}
		}
	}()
}

// PublishLocalJob injects job into in-memory channel
func (r *RedisBus) PublishLocalJob(job *MiningJob) {
	select {
	case r.localJobs <- job:
	default:
	}
}

func (r *RedisBus) Close() {
	r.cancel()
	if r.connected {
		_ = r.client.Close()
	}
}
