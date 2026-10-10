package main

import (
	"log"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.Fatal("SECURITY: deprecated Go/Redis Stratum is disabled; use the node-backed Python bridge")

	cfg := LoadConfig()
	bus := NewRedisBus(cfg)
	defer bus.Close()

	server := NewStratumServer(cfg, bus)

	// Graceful shutdown handling
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		log.Println("Received termination signal. Shutting down Stratum Gateway gracefully...")
		server.Stop()
	}()

	if err := server.Start(); err != nil {
		log.Fatalf("Server fatal error: %v", err)
	}
}
