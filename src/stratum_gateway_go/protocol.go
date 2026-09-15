package main

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"time"
)

// StratumRequest represents standard incoming Stratum V1 / Kaspa JSON-RPC
type StratumRequest struct {
	ID     interface{}   `json:"id"`
	Method string        `json:"method"`
	Params []interface{} `json:"params"`
}

// StratumResponse represents standard outgoing response
type StratumResponse struct {
	ID     interface{} `json:"id"`
	Result interface{} `json:"result"`
	Error  interface{} `json:"error"`
}

// StratumNotification represents server push (set_difficulty, notify)
type StratumNotification struct {
	ID     interface{}   `json:"id"`
	Method string        `json:"method"`
	Params []interface{} `json:"params"`
}

// MiningJob holds template information for mining.notify
type MiningJob struct {
	JobID      string
	Height     uint64
	HeaderHash [32]byte
	TargetDiff float64
	CleanJobs  bool
	CreatedAt  time.Time
}

// FormatIceRiverNotifyPayload builds the mandatory 82-character payload:
// [HeaderHash: 64 hex (32B)] + [TimestampMs: 16 hex (8B little-endian)] + [Clean: 2 hex (1B)]
func FormatIceRiverNotifyPayload(headerHash []byte, clean bool) string {
	hashHex := hex.EncodeToString(headerHash)
	for len(hashHex) < 64 {
		hashHex = "0" + hashHex
	}

	tsMs := uint64(time.Now().UnixNano() / int64(time.Millisecond))
	var tsBytes [8]byte
	binary.LittleEndian.PutUint64(tsBytes[:], tsMs)
	tsHex := hex.EncodeToString(tsBytes[:])

	cleanHex := "00"
	if clean {
		cleanHex = "ff"
	}

	payload := fmt.Sprintf("%s%s%s", hashHex, tsHex, cleanHex)
	return payload
}

func FormatJSON(v interface{}) []byte {
	b, _ := json.Marshal(v)
	return append(b, '\n')
}
