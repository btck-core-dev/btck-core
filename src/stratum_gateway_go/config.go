package main

import (
	"os"
	"strconv"
)

type Config struct {
	ListenPort       string
	RedisAddr        string
	RedisPassword    string
	RedisDB          int
	DefaultDiff      float64
	MinDiff          float64
	MaxDiff          float64
	VardiffTargetSec float64
	VardiffWindow    int
	PoolStealthAddr  string
}

func LoadConfig() *Config {
	port := os.Getenv("STRATUM_PORT")
	if port == "" {
		port = "3334" // Default non-conflicting port for gateway
	}

	redisAddr := os.Getenv("REDIS_ADDR")
	if redisAddr == "" {
		redisAddr = "127.0.0.1:6379"
	}

	defaultDiff := 5.2
	if d := os.Getenv("DEFAULT_DIFFICULTY"); d != "" {
		if val, err := strconv.ParseFloat(d, 64); err == nil {
			defaultDiff = val
		}
	}

	return &Config{
		ListenPort:       ":" + port,
		RedisAddr:        redisAddr,
		RedisPassword:    os.Getenv("REDIS_PASSWORD"),
		RedisDB:          0,
		DefaultDiff:      defaultDiff,
		MinDiff:          2.0,
		MaxDiff:          1024.0,
		VardiffTargetSec: 1.0,
		VardiffWindow:    20,
		PoolStealthAddr:  "btck1s022fd5c7684238a4ee4505c01cef3353279d5330a5901ae135ebbe47a0e47c71a403c6248a22724350e3bfabd6a6280bf50ada1977f0e4f240783d3a03caae334b2d3f0cff62",
	}
}
