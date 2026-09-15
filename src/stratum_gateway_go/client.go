package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"log"
	"net"
	"sync"
	"time"
)

type StratumClient struct {
	conn             net.Conn
	reader           *bufio.Reader
	writer           *bufio.Writer
	writeMu          sync.Mutex
	clientID         string
	walletAddr       string
	workerName       string
	extraNonce1      string
	difficulty       float64
	authorized       bool
	lastShareTime    time.Time
	intervals        []float64
	acceptedShares   uint64
	rejectedShares   uint64
	blocksFound      uint64
	sharesSinceCheck int
}

func NewStratumClient(conn net.Conn, defaultDiff float64) *StratumClient {
	nonceBytes := make([]byte, 4)
	_, _ = rand.Read(nonceBytes)

	return &StratumClient{
		conn:          conn,
		reader:        bufio.NewReaderSize(conn, 4096),
		writer:        bufio.NewWriterSize(conn, 4096),
		clientID:      conn.RemoteAddr().String(),
		extraNonce1:   hex.EncodeToString(nonceBytes),
		difficulty:    defaultDiff,
		lastShareTime: time.Time{},
		intervals:     make([]float64, 0, 30),
	}
}

func (c *StratumClient) SendJSON(v interface{}) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()

	data := FormatJSON(v)
	_, err := c.writer.Write(data)
	if err != nil {
		return err
	}
	return c.writer.Flush()
}

func (c *StratumClient) RecordShare() float64 {
	now := time.Now()
	var interval float64 = 0
	if !c.lastShareTime.IsZero() {
		interval = now.Sub(c.lastShareTime).Seconds()
		c.intervals = append(c.intervals, interval)
		if len(c.intervals) > 30 {
			c.intervals = c.intervals[1:]
		}
	}
	c.lastShareTime = now
	c.acceptedShares++
	c.sharesSinceCheck++
	return interval
}

func (c *StratumClient) SetDifficulty(newDiff float64) error {
	c.difficulty = newDiff
	notif := StratumNotification{
		ID:     nil,
		Method: "mining.set_difficulty",
		Params: []interface{}{c.difficulty},
	}
	log.Printf("⚡ [Vardiff] Miner [%s] diff adjusted -> %.1f", c.clientID, c.difficulty)
	return c.SendJSON(notif)
}

func (c *StratumClient) Close() {
	_ = c.conn.Close()
}
