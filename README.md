# VerriSphere: SDN-Based Multiplayer Game QoS and Congestion Evaluation

VerriSphere is a reproducible Software-Defined Networking (SDN) experiment that evaluates how OpenFlow traffic classification and Open vSwitch (OVS) queuing affect a real-time UDP game flow during network congestion.

The repository contains a small UDP game simulation and telemetry client, an OS-Ken OpenFlow 1.3 controller, a two-host Mininet topology, HTB queue configuration, and an `iperf3` traffic generator.

## What is included

- `controller.py` — OS-Ken controller with reactive MAC learning and queue assignment.
- `mininet_topology.py` — creates the Mininet topology and runs the automated three-scenario benchmark.
- `network_game.py` — UDP game simulation and client-side RTT, jitter, and packet-loss measurements.
- `qos_setup.sh` — configures HTB QoS queues on the switch's host-facing ports.
- `background_traffic.sh` — starts an `iperf3` server or generates competing UDP/TCP traffic.
- `benchmark_summary.json` — captured benchmark measurements and OVS flow/queue-statistics output from a previous run. It is an example result, not a guarantee that a fresh machine will reproduce identical numbers.

## Architecture

```text
                 OS-Ken controller
                 127.0.0.1:6653
                        |
                 OpenFlow 1.3
                        |
                  OVS switch s1
                  /          \
             s1-eth1        s1-eth2
                |              |
          Mininet h1       Mininet h2
          10.0.0.1         10.0.0.2
          UDP game        UDP game client
          iperf3 server   iperf3 client
```

The topology uses two hosts connected to one OVS switch. The controller classifies UDP game traffic on port `5000` into queue 1. The benchmark's comparison game flow uses port `5001`, which is not matched by the game-specific rule and is assigned to queue 2 by the general traffic rule.

## QoS configuration

The QoS script configures Linux HTB on both switch-facing host ports:

| Queue | Purpose | Minimum rate | Maximum rate | OVS queue priority |
|---|---|---:|---:|---:|
| 1 | Game traffic | 7 Mbps | 10 Mbps | 0 |
| 2 | Background / other traffic | 1 Mbps | 10 Mbps | 1 |

The root rate is 10 Mbps. These are HTB rate and priority settings; do not interpret the queue priority field alone as proof of strict-priority scheduling. Unused capacity may be available to other queues according to HTB configuration and traffic conditions.

## Requirements

This project is intended to run on an Ubuntu Linux environment with:

- Python 3
- Mininet
- Open vSwitch with OpenFlow 1.3 support
- OS-Ken
- `iperf3`
- `ovs-ofctl` and `ovs-vsctl`

The topology script expects OS-Ken's `osken-manager` at `~/osken-venv/bin/osken-manager`, with `/usr/local/bin/osken-manager` as a fallback. It also imports Mininet modules, so Mininet must be installed in the Python environment used to run the topology script.

## Installation (Ubuntu)

Install system packages:

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf3 python3 python3-venv
```

Create an OS-Ken virtual environment and install OS-Ken:

```bash
python3 -m venv ~/osken-venv
source ~/osken-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install os-ken
```

Confirm the controller executable exists:

```bash
~/osken-venv/bin/osken-manager --version
```

If Mininet's Python modules are not visible to the interpreter used by the topology script, use the system Python environment that has Mininet installed, or configure an environment where both Mininet and OS-Ken dependencies are available. Do not assume installing OS-Ken into a separate virtual environment also installs Mininet.

Make the helper scripts executable:

```bash
chmod +x qos_setup.sh background_traffic.sh
```

## Run the automated benchmark

From the project directory, run:

```bash
sudo HOME="$HOME" python3 mininet_topology.py --auto
```

The topology script checks for an OS-Ken controller on `127.0.0.1:6653`; if it cannot connect, it attempts to start `~/osken-venv/bin/osken-manager controller.py` (or `/usr/local/bin/osken-manager`). It then creates the Mininet topology, configures QoS, and runs three scenarios.

Useful options:

```bash
sudo HOME="$HOME" python3 mininet_topology.py --auto --ticks 150 --bandwidth 15M --duration 10
```

- `--ticks`: number of game simulation ticks per scenario (default: 150).
- `--bandwidth`: background `iperf3` target rate (default: `15M`).
- `--duration`: background traffic duration in seconds (default: 10).
- `--loglevel`: `debug`, `info`, `warning`, or `error`.

The script prints scenario telemetry and OVS flow/queue statistics. It writes `benchmark_summary.json` in the project directory when the run completes.

## Benchmark scenarios

1. **Baseline:** game traffic on UDP port `5000`, without competing background traffic.
2. **Congested with game QoS classification:** game traffic on UDP port `5000` (queue 1) while `iperf3` sends competing UDP traffic on port `5201` (queue 2).
3. **Congested comparison flow:** game traffic on UDP port `5001` (queue 2) while the same background traffic is active. This is a comparison without the game-specific queue classification; it is not a run with all QoS configuration removed.

## Recorded example results

The included `benchmark_summary.json` records 100 game packets sent in each scenario:

| Metric | Baseline | Congested, game port 5000 | Congested, comparison port 5001 |
|---|---:|---:|---:|
| Average RTT | 17.444 ms | 17.728 ms | 106.552 ms |
| Minimum RTT | 0.193 ms | 0.492 ms | 76.524 ms |
| Maximum RTT | 28.241 ms | 21.738 ms | 381.148 ms |
| Jitter estimate | 5.581 ms | 6.319 ms | 35.761 ms |
| Packet loss | 0% | 0% | 0% |

These values are copied from the included result file. Results depend on the host machine, VM, kernel, Mininet/OVS versions, and run conditions. The reported jitter is the project's estimate based on its RTT samples; it should not be described as an RFC 3550 interarrival-jitter calculation unless the measurement implementation is changed and validated.

## Interactive mode

Run the topology without `--auto` to enter the Mininet CLI:

```bash
sudo HOME="$HOME" python3 mininet_topology.py
```

Example commands inside the Mininet prompt:

```text
mininet> h1 ping -c 3 h2
mininet> h1 python3 network_game.py --role server --host 10.0.0.1 --port 5000 --ticks 0 &
mininet> h2 python3 network_game.py --role client --server-ip 10.0.0.1 --port 5000 --ticks 100
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1
mininet> sh ovs-ofctl -O OpenFlow13 queue-stats s1
mininet> exit
```

For manual background traffic, start the server and client in separate Mininet hosts:

```text
mininet> h1 bash background_traffic.sh server 5201 &
mininet> h2 bash background_traffic.sh client 10.0.0.1 15M 10 udp 5201 &
```

## Run the UDP game outside Mininet

The game program can also be run directly between two reachable machines. On the server:

```bash
python3 network_game.py --role server --host 0.0.0.0 --port 5000 --ticks 0
```

On the client, replace the server address as appropriate:

```bash
python3 network_game.py --role client --server-ip 192.0.2.10 --port 5000 --ticks 200 --output-json result.json
```

The example address `192.0.2.10` is a documentation-only placeholder, not a real server.

## Notes and limitations

- The Mininet topology in this repository is a two-host, one-switch experiment.
- Port `5000` is the game flow specifically classified into queue 1. Port `5001` is used as the comparison flow and is assigned to queue 2 by the general rule.
- The included benchmark JSON is a previously captured run. Re-run the benchmark to generate measurements for your own environment.
- The game is a UDP simulation used to generate interactive traffic and telemetry; it is not a production multiplayer game service.
- This repository contains the Mininet/UDP benchmark implementation. A separate Pygame prototype or standalone HTML dashboard is not included in this source archive.

## License

MIT. See [LICENSE](LICENSE).
