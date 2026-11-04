from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import (
    packet,
    ethernet,
    ipv4,
    udp,
    ether_types,
)


# UDP port used by the multiplayer game
GAME_PORT = 5000

# QoS queue IDs configured on the switch ports
GAME_QUEUE = 1
BACKGROUND_QUEUE = 2

# Ensure OpenFlow protocol handler service is loaded
app_manager.require_app("os_ken.controller.ofp_handler")


class GameController(app_manager.OSKenApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(GameController, self).__init__(*args, **kwargs)

        # Stores the switch's learned MAC-address-to-port mappings.
        # Format: {switch_id: {mac_address: port_number}}
        self.mac_to_port = {}

    # ---------------------------------------------------------
    # Install a flow rule in the switch
    # ---------------------------------------------------------
    def add_flow(
        self,
        datapath,
        priority,
        match,
        actions,
        idle_timeout=30
    ):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        flow_mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions,
            idle_timeout=idle_timeout
        )

        datapath.send_msg(flow_mod)

    # ---------------------------------------------------------
    # Handle a switch connecting to the controller
    # ---------------------------------------------------------
    @set_ev_cls(
        ofp_event.EventOFPSwitchFeatures,
        CONFIG_DISPATCHER
    )
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        self.mac_to_port.setdefault(datapath.id, {})

        # Table-miss rule:
        # Send packets with no matching flow to the controller.
        match = parser.OFPMatch()

        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        self.add_flow(
            datapath=datapath,
            priority=0,
            match=match,
            actions=actions,
            idle_timeout=0
        )

        self.logger.info(
            "Switch connected: datapath ID %s",
            datapath.id
        )

    # ---------------------------------------------------------
    # Handle packets sent by the switch to the controller
    # ---------------------------------------------------------
    @set_ev_cls(
        ofp_event.EventOFPPacketIn,
        MAIN_DISPATCHER
    )
    def packet_in_handler(self, ev):

        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        in_port = msg.match["in_port"]
        dpid = datapath.id

        # Parse the incoming packet.
        pkt = packet.Packet(msg.data)

        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        # Ignore Ethernet frames without a usable Ethernet header.
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src = eth.src
        dst = eth.dst

        # Learn which switch port each source MAC address uses.
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        self.logger.debug(
            "Packet received: switch=%s, src=%s, dst=%s, in_port=%s",
            dpid,
            src,
            dst,
            in_port
        )

        # Decide where the packet should go.
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        # -----------------------------------------------------
        # Classify traffic
        # -----------------------------------------------------

        # Default classification: background traffic.
        queue_id = BACKGROUND_QUEUE
        is_game = False

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)

        # Game packets are UDP packets using port 5000.
        if ip_pkt is not None and udp_pkt is not None:
            if (
                udp_pkt.src_port == GAME_PORT
                or udp_pkt.dst_port == GAME_PORT
            ):
                queue_id = GAME_QUEUE
                is_game = True

        if is_game:
            self.logger.info(
                "GAME TRAFFIC: %s -> %s, queue=%s",
                src,
                dst,
                queue_id
            )

        # -----------------------------------------------------
        # Forward the packet
        # -----------------------------------------------------

        if out_port == ofproto.OFPP_FLOOD:

            # Unknown destination or broadcast:
            # Flood normally so ARP discovery can work.
            actions = [
                parser.OFPActionOutput(
                    ofproto.OFPP_FLOOD
                )
            ]

            self.logger.debug(
                "Flooding packet: src=%s, dst=%s",
                src,
                dst
            )

        else:

            # Known destination:
            # Select the appropriate QoS queue before output.
            actions = [
                parser.OFPActionSetQueue(queue_id),
                parser.OFPActionOutput(out_port)
            ]

            # Build the match for the learned forwarding rule.
            match_fields = {
                "in_port": in_port,
                "eth_src": src,
                "eth_dst": dst
            }

            # Add IP-specific fields when this is an IPv4 packet.
            if ip_pkt is not None:

                match_fields["eth_type"] = ether_types.ETH_TYPE_IP
                match_fields["ip_proto"] = ip_pkt.proto

                # Match UDP ports for UDP traffic.
                if udp_pkt is not None:
                    if is_game:
                        if udp_pkt.dst_port == GAME_PORT:
                            match_fields["udp_dst"] = GAME_PORT
                        elif udp_pkt.src_port == GAME_PORT:
                            match_fields["udp_src"] = GAME_PORT
                    else:
                        match_fields["udp_src"] = udp_pkt.src_port
                        match_fields["udp_dst"] = udp_pkt.dst_port
            else:
                match_fields["eth_type"] = eth.ethertype

            match = parser.OFPMatch(**match_fields)

            # Give game traffic higher flow priority.
            if is_game:
                flow_priority = 20
            else:
                flow_priority = 10

            # Install the forwarding flow.
            self.add_flow(
                datapath=datapath,
                priority=flow_priority,
                match=match,
                actions=actions,
                idle_timeout=30
            )

            self.logger.info(
                "FLOW_INSTALLED: switch=%s, src=%s, dst=%s, "
                "output_port=%s, queue=%s, priority=%s",
                dpid,
                src,
                dst,
                out_port,
                queue_id,
                flow_priority
            )

        # -----------------------------------------------------
        # Send the current packet immediately
        # -----------------------------------------------------

        # A flow rule handles future matching packets.
        # PacketOut forwards the packet that triggered Packet-In.
        data = None

        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        packet_out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        datapath.send_msg(packet_out)
