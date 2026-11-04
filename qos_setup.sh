#!/bin/bash

# Configure Open vSwitch QoS queues for the multiplayer game.
# Run this script while the Mininet topology is running.

set -e

SWITCH="s1"

# Link rate in bits per second: 10 Mbps.
MAX_RATE=10000000

# Minimum bandwidth reservations.
GAME_MIN_RATE=7000000
BACKGROUND_MIN_RATE=1000000

echo "Configuring QoS on switch: $SWITCH"

# Find the switch's existing ports.
PORTS=$(sudo ovs-vsctl list-ports "$SWITCH")

if [ -z "$PORTS" ]; then
    echo "ERROR: No ports found on $SWITCH."
    echo "Start the Mininet topology before running this script."
    exit 1
fi

for PORT in $PORTS; do

    # Skip the switch's local management port.
    if [ "$PORT" = "$SWITCH" ]; then
        continue
    fi

    echo "Configuring QoS on port: $PORT"

    # Remove the existing QoS association, if any.
    sudo ovs-vsctl clear Port "$PORT" qos

    # Create QoS and its two queues.
    sudo ovs-vsctl \
        -- set Port "$PORT" qos=@qos \
        -- --id=@qos create QoS \
            type=linux-htb \
            other-config:max-rate="$MAX_RATE" \
            queues:1=@gameq \
            queues:2=@backgroundq \
        -- --id=@gameq create Queue \
            other-config:min-rate="$GAME_MIN_RATE" \
            other-config:max-rate="$MAX_RATE" \
            other-config:priority=0 \
        -- --id=@backgroundq create Queue \
            other-config:min-rate="$BACKGROUND_MIN_RATE" \
            other-config:max-rate="$MAX_RATE" \
            other-config:priority=1

done

echo
echo "QoS configuration completed."
echo "Game traffic: queue 1"
echo "Background traffic: queue 2"
