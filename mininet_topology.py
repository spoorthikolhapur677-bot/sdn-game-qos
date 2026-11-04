#!/usr/bin/env python3
"""
mininet_topology.py - Mininet SDN Topology with OVS QoS & Automated Benchmarks

Topology Architecture:
    [h1 (10.0.0.1/24)] <---(s1-eth1)---> [s1 (OVS OpenFlow13)] <---(s1-eth2)---> [h2 (10.0.0.2/24)]
                                                |
                                      (OpenFlow 1.3: 6653)
                                                |
                                     [c0 (Remote OS-Ken)]

Features:
- Connects to OS-Ken RemoteController at 127.0.0.1:6653
- Uses OVSKernelSwitch with OpenFlow13 in 'secure' fail-mode
- Pins h1 to s1-eth1 and h2 to s1-eth2 for qos_setup.sh compatibility
- Automatically invokes 'bash qos_setup.sh' on switch startup
- Interactive CLI mode: sudo python3 mininet_topology.py
- Autonomous Benchmark mode: sudo python3 mininet_topology.py --auto
"""

import argparse
import json
import os
import subprocess
import sys
import time
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info, error


# Resolve working directory (prefer ~/sdn-game if present, otherwise current file dir)
SDN_DIR = os.path.expanduser("~/sdn-game")
if not os.path.exists(SDN_DIR):
    SDN_DIR = os.path.dirname(os.path.abspath(__file__))

QOS_SCRIPT = os.path.join(SDN_DIR, "qos_setup.sh")
GAME_SCRIPT = os.path.join(SDN_DIR, "network_game.py")
TRAFFIC_SCRIPT = os.path.join(SDN_DIR, "background_traffic.sh")


def run_command(cmd, check=True):
    """Helper to run a shell command and return stdout."""
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and res.returncode != 0:
        error(f"[Command Failed] {cmd}\nStderr: {res.stderr}\n")
    return res.stdout.strip()


def ensure_controller():
    """Ensures OS-Ken controller is running on 127.0.0.1:6653."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", 6653))
        s.close()
        info("*** Remote Controller OS-Ken is active on 127.0.0.1:6653 ***\n")
        return None
    except Exception:
        s.close()
        info("*** Starting OS-Ken Controller daemon on 127.0.0.1:6653 ***\n")
        ctrl_path = os.path.join(SDN_DIR, "controller.py")
        osken_bin = os.path.expanduser("~/osken-venv/bin/osken-manager")
        if not os.path.exists(osken_bin):
            osken_bin = "/usr/local/bin/osken-manager"
        proc = subprocess.Popen(
            [osken_bin, ctrl_path],
            stdout=open("/tmp/osken_runner.log", "w"),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setpgrp,
        )
        time.sleep(3)
        return proc


def dump_switch_state(label: str):
    """Captures and logs OVS OpenFlow flows and queue statistics."""
    info(f"\n{'=' * 65}\n[OVS STATS] {label}\n{'=' * 65}\n")
    flows = run_command("ovs-ofctl -O OpenFlow13 dump-flows s1", check=False)
    queues = run_command("ovs-ofctl -O OpenFlow13 queue-stats s1", check=False)
    info("--- OpenFlow 1.3 Flow Rules ---\n" + flows + "\n\n")
    info("--- OpenFlow 1.3 Queue Statistics ---\n" + queues + "\n")
    info(f"{'=' * 65}\n\n")
    return flows, queues


def setup_topology():
    """Builds and starts the Mininet network."""
    info("*** Creating Network Topology ***\n")
    net = Mininet(topo=None, build=False, ipBase="10.0.0.0/24")

    info("*** Adding Remote Controller c0 (127.0.0.1:6653) ***\n")
    c0 = net.addController(
        name="c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653,
    )

    info("*** Adding OVS Switch s1 (OpenFlow 1.3, failMode=secure) ***\n")
    s1 = net.addSwitch(
        name="s1",
        cls=OVSKernelSwitch,
        protocols="OpenFlow13",
        failMode="secure",
    )

    info("*** Adding Hosts h1 (10.0.0.1) and h2 (10.0.0.2) ***\n")
    h1 = net.addHost(
        name="h1",
        ip="10.0.0.1/24",
        mac="00:00:00:00:00:01",
    )
    h2 = net.addHost(
        name="h2",
        ip="10.0.0.2/24",
        mac="00:00:00:00:00:02",
    )

    info("*** Creating Links with Explicit Port Pinning ***\n")
    # h1-eth0 <--> s1-eth1 (port 1 on s1)
    net.addLink(h1, s1, port2=1)
    # h2-eth0 <--> s1-eth2 (port 2 on s1)
    net.addLink(h2, s1, port2=2)

    info("*** Starting Network ***\n")
    net.start()
    time.sleep(2)  # Give OVS time to complete OpenFlow handshake with c0

    # Automatically run qos_setup.sh on switch s1
    if os.path.exists(QOS_SCRIPT):
        info(f"*** Applying QoS via {QOS_SCRIPT} ***\n")
        qos_out = run_command(f'sudo bash "{QOS_SCRIPT}"', check=False)
        info(qos_out + "\n")
    else:
        error(f"Warning: QoS script {QOS_SCRIPT} not found.\n")

    return net


def prime_network(net):
    """Pings between hosts to prime ARP and controller MAC-to-port learning."""
    h1, h2 = net.get("h1", "h2")
    info("*** Priming Network ARP and Controller Learning ***\n")
    h1.cmd("ping -c 2 10.0.0.2")
    h2.cmd("ping -c 2 10.0.0.1")
    time.sleep(1)


def cleanup_host_processes(h1, h2):
    """Kills any orphaned background game or iperf3 processes."""
    h1.cmd("pkill -9 -f iperf3 2>/dev/null || true")
    h2.cmd("pkill -9 -f iperf3 2>/dev/null || true")
    h1.cmd("pkill -9 -f network_game.py 2>/dev/null || true")
    h2.cmd("pkill -9 -f network_game.py 2>/dev/null || true")
    time.sleep(1)


def run_benchmark_suite(net, ticks=150, flood_bw="15M", duration=10):
    """
    Executes the three autonomous test scenarios:
      1. Baseline (No congestion, port 5000)
      2. Congested WITH QoS (iperf3 15M flood, game on port 5000 -> Queue 1)
      3. Congested WITHOUT QoS (iperf3 15M flood, game on port 5001 -> Queue 2 / Default)
    """
    h1, h2 = net.get("h1", "h2")
    results = {}

    prime_network(net)
    cleanup_host_processes(h1, h2)

    # ------------------------------------------------------------------
    # Scenario 1: Baseline Run (No Congestion)
    # ------------------------------------------------------------------
    info("\n" + "#" * 65 + "\n")
    info("  SCENARIO 1: BASELINE (NO CONGESTION, PORT 5000)\n")
    info("#" * 65 + "\n")

    json_1 = "/tmp/game_baseline.json"
    h1.cmd(f'rm -f "{json_1}"')

    # Start game server on h1 using popen (run until terminated)
    srv1 = h1.popen(["python3", GAME_SCRIPT, "--role", "server", "--host", "10.0.0.1", "--port", "5000", "--ticks", "0"])
    time.sleep(1.5)

    # Run game client on h2
    info("[Mininet] Running game client on h2...\n")
    client_out1 = h2.cmd(f'python3 "{GAME_SCRIPT}" --role client --server-ip 10.0.0.1 --port 5000 --ticks {ticks} --output-json "{json_1}"')
    print(client_out1)

    try:
        srv1.terminate()
        srv1.wait(timeout=2)
    except Exception:
        pass
    cleanup_host_processes(h1, h2)

    if os.path.exists(json_1):
        with open(json_1, "r") as f:
            results["baseline"] = json.load(f)
    else:
        results["baseline"] = {"error": "Missing baseline JSON"}

    dump_switch_state("Scenario 1: Baseline Completed")

    # ------------------------------------------------------------------
    # Scenario 2: Congested WITH QoS Prioritization (Port 5000 -> Queue 1)
    # ------------------------------------------------------------------
    info("\n" + "#" * 65 + "\n")
    info("  SCENARIO 2: CONGESTED WITH QOS (PORT 5000 -> QUEUE 1)\n")
    info(f"  competing with iperf3 flood ({flood_bw} UDP)\n")
    info("#" * 65 + "\n")

    json_2 = "/tmp/game_qos.json"
    h1.cmd(f'rm -f "{json_2}"')

    # Start iperf3 server on h1
    iperf_srv2 = h1.popen(["bash", TRAFFIC_SCRIPT, "server", "5201"])
    time.sleep(1)

    # Start game server on h1 (port 5000)
    srv2 = h1.popen(["python3", GAME_SCRIPT, "--role", "server", "--host", "10.0.0.1", "--port", "5000", "--ticks", "0"])
    time.sleep(1)

    # Start iperf3 background flood on h2 (flooding 15M UDP to 10.0.0.1)
    info(f"[Mininet] Launching iperf3 background flood ({flood_bw} UDP) from h2 to h1...\n")
    iperf_cli2 = h2.popen(["bash", TRAFFIC_SCRIPT, "client", "10.0.0.1", flood_bw, str(duration + 4), "udp", "5201"])
    time.sleep(2)  # Let contention stabilize in Queue 2

    # Capture switch flow and queue stats during active traffic
    dump_switch_state("Scenario 2: Active Traffic Mid-Run")

    # Run game client on h2
    info("[Mininet] Running game client on h2 during active congestion...\n")
    client_out2 = h2.cmd(f'python3 "{GAME_SCRIPT}" --role client --server-ip 10.0.0.1 --port 5000 --ticks {ticks} --output-json "{json_2}"')
    print(client_out2)

    # Terminate background processes
    try:
        iperf_cli2.terminate()
        iperf_srv2.terminate()
        srv2.terminate()
    except Exception:
        pass
    cleanup_host_processes(h1, h2)

    if os.path.exists(json_2):
        with open(json_2, "r") as f:
            results["congested_with_qos"] = json.load(f)
    else:
        results["congested_with_qos"] = {"error": "Missing QoS JSON"}

    dump_switch_state("Scenario 2: QoS Completed")

    # ------------------------------------------------------------------
    # Scenario 3: Congested WITHOUT QoS Prioritization (Port 5001 -> Queue 2)
    # ------------------------------------------------------------------
    info("\n" + "#" * 65 + "\n")
    info("  SCENARIO 3: CONGESTED WITHOUT QOS (PORT 5001 -> QUEUE 2 / DEFAULT)\n")
    info(f"  competing with iperf3 flood ({flood_bw} UDP)\n")
    info("#" * 65 + "\n")

    json_3 = "/tmp/game_no_qos.json"
    h1.cmd(f'rm -f "{json_3}"')

    # Start iperf3 server on h1
    iperf_srv3 = h1.popen(["bash", TRAFFIC_SCRIPT, "server", "5201"])
    time.sleep(1)

    # Start game server on h1 (port 5001 - non-QoS)
    srv3 = h1.popen(["python3", GAME_SCRIPT, "--role", "server", "--host", "10.0.0.1", "--port", "5001", "--ticks", "0"])
    time.sleep(1)

    # Start iperf3 background flood on h2
    info(f"[Mininet] Launching iperf3 background flood ({flood_bw} UDP) from h2 to h1...\n")
    iperf_cli3 = h2.popen(["bash", TRAFFIC_SCRIPT, "client", "10.0.0.1", flood_bw, str(duration + 4), "udp", "5201"])
    time.sleep(2)  # Let contention stabilize in Queue 2

    # Capture switch flow and queue stats during active traffic
    dump_switch_state("Scenario 3: Active Traffic Mid-Run")

    # Run game client on h2 (connecting on port 5001)
    info("[Mininet] Running game client on h2 during active congestion (Port 5001)...\n")
    client_out3 = h2.cmd(f'python3 "{GAME_SCRIPT}" --role client --server-ip 10.0.0.1 --port 5001 --ticks {ticks} --output-json "{json_3}"')
    print(client_out3)

    # Terminate background processes
    try:
        iperf_cli3.terminate()
        iperf_srv3.terminate()
        srv3.terminate()
    except Exception:
        pass
    cleanup_host_processes(h1, h2)

    if os.path.exists(json_3):
        with open(json_3, "r") as f:
            results["congested_no_qos"] = json.load(f)
    else:
        results["congested_no_qos"] = {"error": "Missing No-QoS JSON"}

    final_flows, final_queues = dump_switch_state("Final Evaluation State")

    # ------------------------------------------------------------------
    # Performance Summary Table
    # ------------------------------------------------------------------
    info("\n" + "=" * 76 + "\n")
    info("            MININET SDN GAME CONGESTION BENCHMARK RESULTS            \n")
    info("=" * 76 + "\n")
    header = f"{'Metric':<25} | {'1. Baseline':<14} | {'2. QoS (Q1)':<14} | {'3. No QoS (Q2)':<14}"
    info(header + "\n" + "-" * 76 + "\n")

    b = results.get("baseline", {})
    q = results.get("congested_with_qos", {})
    nq = results.get("congested_no_qos", {})

    metrics = [
        ("Game Port", "port", ""),
        ("Packets Sent", "total_sent", ""),
        ("Packets Received", "total_received", ""),
        ("Packet Loss (%)", "loss_pct", "%"),
        ("Avg RTT (ms)", "avg_rtt_ms", " ms"),
        ("Min RTT (ms)", "min_rtt_ms", " ms"),
        ("Max RTT (ms)", "max_rtt_ms", " ms"),
        ("Interarrival Jitter", "jitter_ms", " ms"),
    ]

    for label, key, unit in metrics:
        v1 = f"{b.get(key, 'N/A')}{unit}" if key in b else "N/A"
        v2 = f"{q.get(key, 'N/A')}{unit}" if key in q else "N/A"
        v3 = f"{nq.get(key, 'N/A')}{unit}" if key in nq else "N/A"
        info(f"{label:<25} | {v1:<14} | {v2:<14} | {v3:<14}\n")

    info("=" * 76 + "\n\n")

    # Save complete benchmark output to file
    summary_path = os.path.join(SDN_DIR, "benchmark_summary.json")
    try:
        with open(summary_path, "w") as f:
            json.dump({
                "benchmarks": results,
                "flow_dump": final_flows,
                "queue_stats": final_queues,
            }, f, indent=2)
        info(f"[Mininet] Benchmark summary saved to {summary_path}\n")
    except Exception as e:
        error(f"Failed to write summary: {e}\n")

    return results


def main():
    parser = argparse.ArgumentParser(description="Mininet SDN Topology with OVS QoS Integration")
    parser.add_argument("--auto", action="store_true", help="Execute automated congestion benchmark suite")
    parser.add_argument("--ticks", type=int, default=150, help="Number of ticks for game runs in auto mode")
    parser.add_argument("--bandwidth", default="15M", help="Bandwidth rate for iperf3 contention")
    parser.add_argument("--duration", type=int, default=10, help="Contention duration in seconds")
    parser.add_argument("--loglevel", default="info", choices=["debug", "info", "warning", "error"])

    args = parser.parse_args()
    setLogLevel(args.loglevel)

    ctrl_proc = ensure_controller()
    net = setup_topology()
    try:
        if args.auto:
            run_benchmark_suite(net, ticks=args.ticks, flood_bw=args.bandwidth, duration=args.duration)
        else:
            info("*** Running Mininet Interactive CLI ***\n")
            CLI(net)
    finally:
        info("*** Stopping Network ***\n")
        net.stop()
        if ctrl_proc:
            try:
                os.killpg(os.getpgid(ctrl_proc.pid), 9)
            except Exception:
                pass
        run_command("sudo mn -c", check=False)


if __name__ == "__main__":
    main()
