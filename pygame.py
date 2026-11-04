import socket
import json
import pygame
import sys
import time

# ================= SETTINGS =================

IP = "127.0.0.1"
PORT = 5000

WIDTH = 1000
HEIGHT = 600

SPEED = 5
BULLET_SPEED = 12
DAMAGE = 20
RESPAWN_TIME = 2

COLORS = {
    "P1": (50, 150, 255),
    "P2": (255, 70, 70),
    "P3": (60, 220, 100)
}

START = {
    "P1": [150, 300],
    "P2": [500, 300],
    "P3": [850, 300]
}

# W, S, A, D, Shoot
KEYS = {
    "P1": (pygame.K_w, pygame.K_s, pygame.K_a,
           pygame.K_d, pygame.K_SPACE),

    "P2": (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT,
           pygame.K_RIGHT, pygame.K_RETURN),

    "P3": (pygame.K_i, pygame.K_k, pygame.K_j,
           pygame.K_l, pygame.K_o)
}


# =========================================================
# SERVER
# =========================================================

def server():

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((IP, PORT))

    s.settimeout(0.01)

    players = {}
    bullets = []

    print("================================")
    print("      UDP GAME SERVER")
    print("================================")
    print("Port:", PORT)

    while True:

        # ---------------- RECEIVE DATA ----------------

        try:

            data, addr = s.recvfrom(4096)
            msg = json.loads(data.decode())

            pid = msg["id"]

            if pid not in players:

                players[pid] = {
                    "x": START[pid][0],
                    "y": START[pid][1],
                    "hp": 100,
                    "kills": 0,
                    "alive": True,
                    "respawn": 0,
                    "addr": addr
                }

                print(pid, "connected")

            p = players[pid]

            p["x"] = msg["x"]
            p["y"] = msg["y"]
            p["addr"] = addr

            # ---------------- SHOOT ----------------

            if msg["shoot"] and p["alive"]:

                direction = msg.get("direction", 1)

                bullets.append({
                    "x": p["x"] + direction * 25,
                    "y": p["y"],
                    "dx": direction * BULLET_SPEED,
                    "owner": pid
                })

        except socket.timeout:
            pass

        except ConnectionResetError:
            continue

        except (json.JSONDecodeError, KeyError):
            continue

        # ---------------- BULLET MOVEMENT ----------------

        new_bullets = []

        for bullet in bullets:

            bullet["x"] += bullet["dx"]

            # Remove bullet outside screen
            if bullet["x"] < 0 or bullet["x"] > WIDTH:
                continue

            hit = False

            # Check collision
            for pid, p in players.items():

                if pid == bullet["owner"]:
                    continue

                if not p["alive"]:
                    continue

                distance_x = abs(
                    bullet["x"] - p["x"]
                )

                distance_y = abs(
                    bullet["y"] - p["y"]
                )

                if distance_x < 25 and distance_y < 25:

                    p["hp"] -= DAMAGE
                    hit = True

                    print(
                        bullet["owner"],
                        "hit",
                        pid,
                        "HP:",
                        p["hp"]
                    )

                    # Player killed
                    if p["hp"] <= 0:

                        p["hp"] = 0
                        p["alive"] = False
                        p["respawn"] = (
                            time.time() + RESPAWN_TIME
                        )

                        players[
                            bullet["owner"]
                        ]["kills"] += 1

                        print(
                            pid,
                            "was eliminated by",
                            bullet["owner"]
                        )

                    break

            if not hit:
                new_bullets.append(bullet)

        bullets = new_bullets

        # ---------------- RESPAWN ----------------

        for pid, p in players.items():

            if not p["alive"]:

                if time.time() >= p["respawn"]:

                    p["x"] = START[pid][0]
                    p["y"] = START[pid][1]

                    p["hp"] = 100
                    p["alive"] = True

                    print(pid, "respawned")

        # ---------------- GAME STATE ----------------

        state = {
            "players": {},
            "bullets": []
        }

        for pid, p in players.items():

            state["players"][pid] = {
                "x": p["x"],
                "y": p["y"],
                "hp": p["hp"],
                "kills": p["kills"],
                "alive": p["alive"]
            }

        for bullet in bullets:

            state["bullets"].append({
                "x": bullet["x"],
                "y": bullet["y"]
            })

        packet = json.dumps(state).encode()

        # ---------------- SEND TO PLAYERS ----------------

        for p in players.values():

            try:
                s.sendto(packet, p["addr"])
            except OSError:
                pass


# =========================================================
# CLIENT
# =========================================================

def client(pid):

    pygame.init()

    screen = pygame.display.set_mode(
        (WIDTH, HEIGHT)
    )

    pygame.display.set_caption(
        "UDP Multiplayer - " + pid
    )

    clock = pygame.time.Clock()

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    sock.setblocking(False)

    x, y = START[pid]

    players = {}
    bullets = []

    up, down, left, right, shoot = KEYS[pid]

    direction = 1

    last_shot = 0
    shoot_delay = 0.25

    running = True

    while running:

        shooting = False

        # ---------------- EVENTS ----------------

        for event in pygame.event.get():

            if event.type == pygame.QUIT:
                running = False

        keys = pygame.key.get_pressed()

        # ---------------- MOVEMENT ----------------

        if keys[up]:
            y -= SPEED

        if keys[down]:
            y += SPEED

        if keys[left]:

            x -= SPEED
            direction = -1

        if keys[right]:

            x += SPEED
            direction = 1

        # Keep player inside arena

        x = max(
            30,
            min(WIDTH - 30, x)
        )

        y = max(
            100,
            min(HEIGHT - 30, y)
        )

        # ---------------- SHOOT ----------------

        if keys[shoot]:

            current_time = time.time()

            if current_time - last_shot >= shoot_delay:

                shooting = True
                last_shot = current_time

        # ---------------- SEND DATA ----------------

        message = {
            "id": pid,
            "x": x,
            "y": y,
            "shoot": shooting,
            "direction": direction
        }

        try:

            sock.sendto(
                json.dumps(message).encode(),
                (IP, PORT)
            )

        except OSError:
            pass

        # ---------------- RECEIVE DATA ----------------

        try:

            while True:

                data, _ = sock.recvfrom(65535)

                state = json.loads(
                    data.decode()
                )

                players = state["players"]
                bullets = state["bullets"]

        except BlockingIOError:
            pass

        except ConnectionResetError:
            pass

        except (json.JSONDecodeError, KeyError):
            pass

        # =================================================
        # DRAW
        # =================================================

        screen.fill((15, 15, 20))

        # Grid

        for gx in range(0, WIDTH, 50):

            pygame.draw.line(
                screen,
                (30, 30, 38),
                (gx, 80),
                (gx, HEIGHT)
            )

        for gy in range(80, HEIGHT, 50):

            pygame.draw.line(
                screen,
                (30, 30, 38),
                (0, gy),
                (WIDTH, gy)
            )

        font = pygame.font.Font(
            None,
            28
        )

        title = font.render(
            "UDP MULTIPLAYER   YOU: " + pid,
            True,
            (255, 255, 255)
        )

        screen.blit(title, (20, 20))

        # ---------------- PLAYERS ----------------

        for p, data in players.items():

            if not data["alive"]:
                continue

            px = int(data["x"])
            py = int(data["y"])

            color = COLORS[p]

            # Body

            pygame.draw.rect(
                screen,
                color,
                (px - 15, py - 10, 30, 35),
                border_radius=6
            )

            # Head

            pygame.draw.circle(
                screen,
                (224, 177, 130),
                (px, py - 22),
                11
            )

            # Gun

            gun_direction = direction if p == pid else 1

            pygame.draw.line(
                screen,
                (90, 90, 90),
                (px, py - 5),
                (
                    px + gun_direction * 35,
                    py - 5
                ),
                6
            )

            # Name

            name = font.render(
                p,
                True,
                (255, 255, 255)
            )

            screen.blit(
                name,
                (px - 10, py + 30)
            )

            # Health bar

            pygame.draw.rect(
                screen,
                (60, 60, 60),
                (px - 25, py + 45, 50, 7)
            )

            pygame.draw.rect(
                screen,
                (50, 220, 70),
                (
                    px - 25,
                    py + 45,
                    data["hp"] // 2,
                    7
                )
            )

            # Kill count

            kill_text = pygame.font.Font(
                None,
                20
            ).render(
                "K:" + str(data["kills"]),
                True,
                (255, 255, 255)
            )

            screen.blit(
                kill_text,
                (px - 12, py + 55)
            )

        # ---------------- BULLETS ----------------

        for bullet in bullets:

            pygame.draw.circle(
                screen,
                (255, 220, 40),
                (
                    int(bullet["x"]),
                    int(bullet["y"])
                ),
                5
            )

        # ---------------- CONTROLS ----------------

        small = pygame.font.Font(
            None,
            22
        )

        if pid == "P1":

            text = "WASD = Move | SPACE = Shoot"

        elif pid == "P2":

            text = "ARROWS = Move | ENTER = Shoot"

        else:

            text = "IJKL = Move | O = Shoot"

        screen.blit(
            small.render(
                text,
                True,
                (255, 255, 255)
            ),
            (20, HEIGHT - 30)
        )

        pygame.display.flip()

        clock.tick(60)

    sock.close()
    pygame.quit()


# =========================================================
# MAIN
# =========================================================

if len(sys.argv) < 2:

    print("Usage:")
    print("python network_game.py server")
    print("python network_game.py 1")
    print("python network_game.py 2")
    print("python network_game.py 3")
    sys.exit()

mode = sys.argv[1]

if mode == "server":

    server()

elif mode == "1":

    client("P1")

elif mode == "2":

    client("P2")

elif mode == "3":

    client("P3")

else:

    print("Invalid option")