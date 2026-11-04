#!/usr/bin/env python3
"""
network_game.py - SDN Multiplayer Game & Telemetry Engine

Simulates a real-time multiplayer 2D arena game ("PacketArena") over UDP.
Features client-side telemetry tracking Round-Trip Time (RTT), jitter,
and packet loss under varying network conditions.

Designed for Mininet OpenFlow / QoS evaluation:
- Default UDP Port: 5000 (Matches OpenFlow priority-20 QoS rule -> Queue 1)
- Non-QoS Test Port: 5001 (Misses rule -> falls into Queue 2 / default queue)
"""

import argparse
import json
import math
import os
import random
import select
import socket
import struct
import sys
import time
from typing import Dict, Any, List, Optional


# ----------------------------------------------------------------------
# Packet Protocol Definitions
# ----------------------------------------------------------------------
# Header format:
# Magic: 2 bytes (0x53, 0x47 = 'SG' for SDN Game)
# Type: 1 byte (1 = CLIENT_INPUT, 2 = SERVER_STATE, 3 = PING, 4 = PONG)
# Seq: 4 bytes (unsigned int)
# AckSeq: 4 bytes (unsigned int)
# Timestamp: 8 bytes (double, client send timestamp in seconds)
# Payload Length: 2 bytes (unsigned short)
HEADER_FORMAT = "!2sBII dH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
MAGIC = b"SG"

TYPE_CLIENT_INPUT = 1
TYPE_SERVER_STATE = 2
TYPE_PING = 3
TYPE_PONG = 4


def pack_packet(pkt_type: int, seq: int, ack_seq: int, timestamp: float, payload: bytes) -> bytes:
    header = struct.pack(HEADER_FORMAT, MAGIC, pkt_type, seq, ack_seq, timestamp, len(payload))
    return header + payload


def unpack_packet(data: bytes):
    if len(data) < HEADER_SIZE:
        return None, None
    magic, pkt_type, seq, ack_seq, timestamp, payload_len = struct.unpack(
        HEADER_FORMAT, data[:HEADER_SIZE]
    )
    if magic != MAGIC:
        return None, None
    payload = data[HEADER_SIZE : HEADER_SIZE + payload_len]
    header = {
        "type": pkt_type,
        "seq": seq,
        "ack_seq": ack_seq,
        "timestamp": timestamp,
        "payload_len": payload_len,
    }
    return header, payload


# ----------------------------------------------------------------------
# Game State Logic
# ----------------------------------------------------------------------
class GameWorld:
    """Simulates 2D game arena with players, movement physics, and targets."""

    def __init__(self, width: float = 800.0, height: float = 600.0):
        self.width = width
        self.height = height
        self.tick = 0
        self.players: Dict[str, Dict[str, Any]] = {}
        self.targets: List[Dict[str, float]] = []
        self._spawn_targets(5)

    def _spawn_targets(self, count: int):
        for _ in range(count):
            self.targets.append({
                "x": round(random.uniform(50.0, self.width - 50.0), 1),
                "y": round(random.uniform(50.0, self.height - 50.0), 1),
                "radius": 15.0,
            })

    def register_player(self, player_id: str):
        if player_id not in self.players:
            self.players[player_id] = {
                "x": self.width / 2.0,
                "y": self.height / 2.0,
                "vx": 0.0,
                "vy": 0.0,
                "score": 0,
            }

    def update_player_input(self, player_id: str, dx: float, dy: float, speed: float = 8.0):
        if player_id not in self.players:
            self.register_player(player_id)
        p = self.players[player_id]
        length = math.hypot(dx, dy)
        if length > 0.001:
            p["vx"] = (dx / length) * speed
            p["vy"] = (dy / length) * speed
        else:
            p["vx"] = 0.0
            p["vy"] = 0.0

    def step(self):
        self.tick += 1
        # Update player positions
        for p in self.players.values():
            p["x"] = max(10.0, min(self.width - 10.0, p["x"] + p["vx"]))
            p["y"] = max(10.0, min(self.height - 10.0, p["y"] + p["vy"]))

            # Check target collisions
            for t in list(self.targets):
                if math.hypot(p["x"] - t["x"], p["y"] - t["y"]) < (15.0 + t["radius"]):
                    p["score"] += 10
                    self.targets.remove(t)
                    self._spawn_targets(1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick": self.tick,
            "players": self.players,
            "target_count": len(self.targets),
        }


# ----------------------------------------------------------------------
# Game Server
# ----------------------------------------------------------------------
class GameServer:
    def __init__(self, host: str, port: int, tick_rate: int = 50):
        self.host = host
        self.port = port
        self.tick_interval = 1.0 / tick_rate
        self.world = GameWorld()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Allow immediate socket reuse
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.setblocking(False)
        self.server_seq = 0
        self.clients: Dict[str, tuple] = {}
        print(f"[Server] Game server listening on UDP {self.host}:{self.port} (Tick rate: {tick_rate}Hz)")

    def run(self, max_ticks: int = 0):
        ticks_executed = 0
        running = True
        try:
            while running:
                start_time = time.time()

                # Process all pending network packets
                while True:
                    try:
                        data, addr = self.sock.recvfrom(4096)
                    except BlockingIOError:
                        break

                    header, payload = unpack_packet(data)
                    if not header:
                        continue

                    client_key = f"{addr[0]}:{addr[1]}"
                    self.clients[client_key] = addr

                    if header["type"] == TYPE_CLIENT_INPUT:
                        try:
                            input_data = json.loads(payload.decode("utf-8"))
                            player_id = input_data.get("player_id", client_key)
                            dx = input_data.get("dx", 0.0)
                            dy = input_data.get("dy", 0.0)
                            self.world.update_player_input(player_id, dx, dy)
                        except Exception:
                            pass

                        # Immediate or tick-based response containing ack
                        self.server_seq += 1
                        state_payload = json.dumps(self.world.to_dict()).encode("utf-8")
                        response = pack_packet(
                            pkt_type=TYPE_SERVER_STATE,
                            seq=self.server_seq,
                            ack_seq=header["seq"],
                            timestamp=header["timestamp"],  # Echo client timestamp for RTT
                            payload=state_payload,
                        )
                        self.sock.sendto(response, addr)

                # Physics step
                self.world.step()
                ticks_executed += 1

                if max_ticks > 0 and ticks_executed >= max_ticks:
                    print(f"[Server] Completed {max_ticks} ticks. Exiting.")
                    break

                elapsed = time.time() - start_time
                sleep_time = self.tick_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("\n[Server] Interrupted by user. Shutting down.")
        finally:
            self.sock.close()


# ----------------------------------------------------------------------
# Game Client with Telemetry
# ----------------------------------------------------------------------
class TelemetryTracker:
    def __init__(self):
        self.rtt_samples: List[float] = []
        self.total_sent = 0
        self.total_received = 0
        self.jitter = 0.0
        self.last_rtt = None

    def record_send(self):
        self.total_sent += 1

    def record_receive(self, rtt_ms: float):
        self.total_received += 1
        self.rtt_samples.append(rtt_ms)

        # RFC 3550 interarrival jitter estimation
        if self.last_rtt is not None:
            diff = abs(rtt_ms - self.last_rtt)
            self.jitter += (diff - self.jitter) / 16.0
        else:
            self.jitter = 0.0
        self.last_rtt = rtt_ms

    def get_stats(self) -> Dict[str, Any]:
        lost = self.total_sent - self.total_received
        loss_pct = (lost / self.total_sent * 100.0) if self.total_sent > 0 else 0.0
        min_rtt = min(self.rtt_samples) if self.rtt_samples else 0.0
        max_rtt = max(self.rtt_samples) if self.rtt_samples else 0.0
        avg_rtt = (sum(self.rtt_samples) / len(self.rtt_samples)) if self.rtt_samples else 0.0

        return {
            "total_sent": self.total_sent,
            "total_received": self.total_received,
            "packets_lost": lost,
            "loss_pct": round(loss_pct, 2),
            "min_rtt_ms": round(min_rtt, 3),
            "avg_rtt_ms": round(avg_rtt, 3),
            "max_rtt_ms": round(max_rtt, 3),
            "jitter_ms": round(self.jitter, 3),
        }


class GameClient:
    def __init__(
        self,
        server_ip: str,
        port: int,
        client_ip: Optional[str] = None,
        tick_rate: int = 50,
        player_id: str = "player_1",
    ):
        self.server_ip = server_ip
        self.port = port
        self.tick_interval = 1.0 / tick_rate
        self.player_id = player_id
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if client_ip:
            self.sock.bind((client_ip, 0))
        self.sock.setblocking(False)
        self.seq = 0
        self.telemetry = TelemetryTracker()

        # Simulated movement pattern (swirling circular motion)
        self.angle = 0.0

    def run(self, ticks: int = 200, output_json: Optional[str] = None) -> Dict[str, Any]:
        print(f"[Client] Connecting to Game Server at {self.server_ip}:{self.port}...")
        print(f"[Client] Running for {ticks} ticks at {1.0 / self.tick_interval:.1f} Hz...")

        start_time = time.time()
        for tick in range(1, ticks + 1):
            tick_start = time.time()
            self.seq += 1

            # Simulated joystick movement
            self.angle += 0.1
            dx = round(math.cos(self.angle), 2)
            dy = round(math.sin(self.angle), 2)

            input_payload = json.dumps({
                "player_id": self.player_id,
                "dx": dx,
                "dy": dy,
                "tick": tick,
            }).encode("utf-8")

            send_ts = time.time()
            pkt = pack_packet(
                pkt_type=TYPE_CLIENT_INPUT,
                seq=self.seq,
                ack_seq=0,
                timestamp=send_ts,
                payload=input_payload,
            )

            try:
                self.sock.sendto(pkt, (self.server_ip, self.port))
                self.telemetry.record_send()
            except Exception as e:
                print(f"[Client] Send error: {e}")

            # Wait for response with timeout up to tick_interval
            ready = select.select([self.sock], [], [], max(0.001, self.tick_interval - 0.002))
            if ready[0]:
                while True:
                    try:
                        data, addr = self.sock.recvfrom(4096)
                        recv_ts = time.time()
                        header, payload = unpack_packet(data)
                        if header and header["type"] == TYPE_SERVER_STATE:
                            rtt_ms = (recv_ts - header["timestamp"]) * 1000.0
                            self.telemetry.record_receive(rtt_ms)
                    except (BlockingIOError, socket.error):
                        break
                    except Exception:
                        break

            # Live telemetry output every 25 ticks
            if tick % 25 == 0 or tick == ticks:
                stats = self.telemetry.get_stats()
                print(
                    f"[Tick {tick:03d}/{ticks:03d}] Sent: {stats['total_sent']} | "
                    f"Recv: {stats['total_received']} | Loss: {stats['loss_pct']:5.1f}% | "
                    f"RTT (avg): {stats['avg_rtt_ms']:6.2f} ms | Jitter: {stats['jitter_ms']:5.2f} ms"
                )

            # Sleep remaining tick time
            elapsed = time.time() - tick_start
            sleep_time = self.tick_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Allow extra time for in-flight packets to arrive
        time.sleep(0.3)
        while True:
            ready = select.select([self.sock], [], [], 0.05)
            if not ready[0]:
                break
            try:
                data, _ = self.sock.recvfrom(4096)
                recv_ts = time.time()
                header, _ = unpack_packet(data)
                if header and header["type"] == TYPE_SERVER_STATE:
                    rtt_ms = (recv_ts - header["timestamp"]) * 1000.0
                    self.telemetry.record_receive(rtt_ms)
            except Exception:
                break

        self.sock.close()
        duration = round(time.time() - start_time, 2)
        final_stats = self.telemetry.get_stats()
        final_stats["duration_s"] = duration
        final_stats["port"] = self.port
        final_stats["server_ip"] = self.server_ip

        print("\n" + "=" * 55)
        print("          GAME CLIENT TELEMETRY SUMMARY          ")
        print("=" * 55)
        print(f" Target Server       : {self.server_ip}:{self.port}")
        print(f" Test Duration       : {duration} s ({ticks} ticks)")
        print(f" Packets Sent / Recv : {final_stats['total_sent']} / {final_stats['total_received']}")
        print(f" Packet Loss         : {final_stats['loss_pct']} %")
        print(f" Min RTT             : {final_stats['min_rtt_ms']} ms")
        print(f" Avg RTT             : {final_stats['avg_rtt_ms']} ms")
        print(f" Max RTT             : {final_stats['max_rtt_ms']} ms")
        print(f" Interarrival Jitter : {final_stats['jitter_ms']} ms")
        print("=" * 55 + "\n")

        if output_json:
            try:
                with open(output_json, "w") as f:
                    json.dump(final_stats, f, indent=2)
                print(f"[Client] Saved telemetry report to {output_json}")
            except Exception as e:
                print(f"[Client] Failed to save JSON report: {e}")

        return final_stats


# ----------------------------------------------------------------------
# CLI Entrypoint
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="SDN Multiplayer Network Game & Telemetry Engine"
    )
    parser.add_argument(
        "--role",
        choices=["server", "client"],
        default="server",
        help="Run role: server or client (default: server)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host IP address to bind (server mode, default: 0.0.0.0)",
    )
    parser.add_argument(
        "--server-ip",
        default="10.0.0.1",
        help="Game server IPv4 address (default: 10.0.0.1)",
    )
    parser.add_argument(
        "--client-ip",
        default="10.0.0.2",
        help="Game client IPv4 address (default: 10.0.0.2)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Game UDP port (default: 5000; use 5001 to test non-QoS flow)",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=200,
        help="Number of simulation ticks to run (default: 200, 0 for infinite)",
    )
    parser.add_argument(
        "--tick-rate",
        type=int,
        default=50,
        help="Tick rate in Hz / ticks per second (default: 50)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Path to write JSON telemetry results",
    )

    args = parser.parse_args()

    if args.role == "server":
        bind_addr = args.host if args.host != "0.0.0.0" else "0.0.0.0"
        server = GameServer(host=bind_addr, port=args.port, tick_rate=args.tick_rate)
        server.run(max_ticks=args.ticks)
    elif args.role == "client":
        client = GameClient(
            server_ip=args.server_ip,
            port=args.port,
            client_ip=args.client_ip if args.client_ip != "0.0.0.0" else None,
            tick_rate=args.tick_rate,
        )
        client.run(ticks=args.ticks, output_json=args.output_json)


if __name__ == "__main__":
    main()
