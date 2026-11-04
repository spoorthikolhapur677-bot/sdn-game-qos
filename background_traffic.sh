#!/bin/bash
# ==============================================================================
# background_traffic.sh - iperf3 Background Contention Traffic Generator
# ==============================================================================
# Generates competing UDP or TCP background traffic on port 5201 to saturate
# switch links (10 Mbps HTB root) and create queue congestion for non-prioritized flows.
#
# Usage:
#   Server Mode:
#     ./background_traffic.sh server [port]
#   Client Flood Mode:
#     ./background_traffic.sh client <target_ip> <bandwidth> <duration> [udp|tcp] [port]
#
# Examples:
#   ./background_traffic.sh server
#   ./background_traffic.sh client 10.0.0.1 15M 10 udp
# ==============================================================================

set -e

MODE="${1:-}"

print_usage() {
    echo "Usage:"
    echo "  Server: $0 server [port]"
    echo "  Client: $0 client <target_ip> <bandwidth> <duration> [udp|tcp] [port]"
    echo ""
    echo "Parameters:"
    echo "  target_ip  : Destination IP address (e.g., 10.0.0.1)"
    echo "  bandwidth  : Target bandwidth rate (e.g., 15M, 20M, 100M)"
    echo "  duration   : Test duration in seconds (e.g., 10, 30)"
    echo "  proto      : Protocol, either 'udp' or 'tcp' (default: udp)"
    echo "  port       : Port number (default: 5201)"
    exit 1
}

if [ -z "$MODE" ]; then
    print_usage
fi

if [ "$MODE" = "server" ]; then
    PORT="${2:-5201}"
    echo "[background_traffic] Starting iperf3 server on port $PORT..."
    # Kill any existing iperf3 server on this port
    pkill -f "iperf3.*-s.*-p $PORT" 2>/dev/null || true
    exec iperf3 -s -p "$PORT"

elif [ "$MODE" = "client" ]; then
    TARGET_IP="${2:-}"
    BANDWIDTH="${3:-15M}"
    DURATION="${4:-10}"
    PROTO="${5:-udp}"
    PORT="${6:-5201}"

    if [ -z "$TARGET_IP" ]; then
        echo "Error: target_ip is required for client mode."
        print_usage
    fi

    echo "[background_traffic] Starting iperf3 client traffic..."
    echo "  Target    : $TARGET_IP:$PORT"
    echo "  Bandwidth : $BANDWIDTH"
    echo "  Duration  : $DURATION s"
    echo "  Protocol  : $PROTO"

    if [ "$PROTO" = "udp" ]; then
        exec iperf3 -c "$TARGET_IP" -p "$PORT" -u -b "$BANDWIDTH" -t "$DURATION"
    elif [ "$PROTO" = "tcp" ]; then
        exec iperf3 -c "$TARGET_IP" -p "$PORT" -b "$BANDWIDTH" -t "$DURATION"
    else
        echo "Error: Invalid protocol '$PROTO'. Choose 'udp' or 'tcp'."
        exit 1
    fi

else
    echo "Error: Unknown mode '$MODE'."
    print_usage
fi
