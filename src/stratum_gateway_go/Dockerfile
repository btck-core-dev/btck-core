# Build Stage
FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod ./
RUN go mod download || true
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o stratum-gateway .

# Final Minimal Distroless Image
FROM alpine:3.19
RUN apk --no-cache add ca-certificates
WORKDIR /app
COPY --from=builder /app/stratum-gateway .

EXPOSE 3333
ENTRYPOINT ["/app/stratum-gateway"]
