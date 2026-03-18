"""
Live runtime tests for LXCF over real Reticulum transport.

These tests are shelved (xfail) because running two RNS instances on
the same machine is not practical:

- Standalone instances conflict on AutoInterface ports (second process
  exits with rc=255).
- Shared-instance mode relays PLAIN broadcasts between local clients
  but NOT GROUP packets (Transport.inbound limitation), and SINGLE
  delivery requires link establishment which also needs two distinct
  network-reachable instances.

All tests are decorated with @pytest.mark.live and excluded from
default pytest runs.  Run explicitly with:

    .venv/bin/python -m pytest -m live tests/live/test_live.py -v -s
"""

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skip(
        reason="Shelved: two RNS instances cannot coexist on one machine. "
               "Standalone instances conflict on AutoInterface ports; "
               "shared instances do not relay GROUP packets between local clients.",
    ),
]

VENV_BIN = os.path.dirname(sys.executable)
RNSD_PATH = os.path.join(VENV_BIN, "rnsd")
PYTHON_PATH = sys.executable

RESULT_TIMEOUT = 30
POLL_INTERVAL = 0.25

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def poll_for_file(path, timeout=RESULT_TIMEOUT, interval=POLL_INTERVAL):
    """Wait until *path* exists and contains valid JSON, return parsed data."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        time.sleep(interval)
    raise TimeoutError(f"Timed out waiting for {path}")


def run_client(script: str, shared_dir: str, client_id: str,
               config_dir: str = "", cwd: str = "."):
    """Launch a client subprocess with its own LXMF storage but shared RNS config."""
    env = os.environ.copy()
    env["SHARED_DIR"] = shared_dir
    env["CLIENT_ID"] = client_id
    env["RNS_CONFIG_DIR"] = config_dir
    return subprocess.Popen(
        [PYTHON_PATH, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )


def wait_and_check(proc, label="client", timeout=RESULT_TIMEOUT + 10):
    """Wait for subprocess to finish; raise on non-zero exit."""
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise TimeoutError(f"{label} subprocess timed out")
    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"{label} subprocess failed (rc={proc.returncode}):\n"
            f"STDOUT:\n{out}\nSTDERR:\n{err}"
        )
    if out.strip():
        print(f"\n[{label} stdout]: {out.strip()}")
    if err.strip():
        print(f"\n[{label} stderr]: {err.strip()}")
    return out, err


# Common preamble injected into every client script.
_PREAMBLE = textwrap.dedent("""\
import json, os, sys, time
sys.path.insert(0, os.getcwd())

shared = os.environ["SHARED_DIR"]
client_id = os.environ["CLIENT_ID"]
rns_config_dir = os.environ["RNS_CONFIG_DIR"]

import RNS, LXMF
from lxcf import Client

RNS.Reticulum(configdir=rns_config_dir)

store = os.path.join(shared, f"store_{client_id}")
os.makedirs(store, exist_ok=True)
""")


# ------------------------------------------------------------------
# Session-scoped fixture: rnsd subprocess
# ------------------------------------------------------------------

@pytest.fixture(scope="session")
def rnsd_instance(tmp_path_factory):
    """Start rnsd for the test session, yield config path, teardown after."""
    tmp = tmp_path_factory.mktemp("rns_live")
    config_dir = tmp / "config"
    config_dir.mkdir()

    (config_dir / "config").write_text(textwrap.dedent("""\
        [reticulum]
          enable_transport = true
          share_instance = true
          shared_instance_port = 37428

        [interfaces]
          [[Default Interface]]
            type = AutoInterface
            enabled = true
    """))

    proc = subprocess.Popen(
        [RNSD_PATH, "--config", str(config_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(3)
    yield str(config_dir)

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


@pytest.fixture
def shared_dir(tmp_path):
    """A temp directory for inter-process communication via JSON files."""
    return str(tmp_path)


# ------------------------------------------------------------------
# Test 10.2: Two-client channel messaging
# ------------------------------------------------------------------

_SCRIPT_B_MSG = _PREAMBLE + textwrap.dedent("""\
id_b = RNS.Identity()
router_b = LXMF.LXMRouter(identity=id_b, storagepath=store)
dest_b = router_b.register_delivery_identity(id_b, display_name="bob")
client_b = Client(router=router_b, destination=dest_b, nick="bob")

received = []

@client_b.on_message
def on_msg(channel, msg):
    received.append({"body": msg.body, "nick": msg.nick})

ch_b = client_b.join("#test")

with open(os.path.join(shared, "b_ready.json"), "w") as f:
    json.dump({"ready": True}, f)

deadline = time.time() + 25
while not received and time.time() < deadline:
    time.sleep(0.1)

with open(os.path.join(shared, "b_result.json"), "w") as f:
    json.dump({"received": received}, f)
""")

_SCRIPT_A_MSG = _PREAMBLE + textwrap.dedent("""\
id_a = RNS.Identity()
router_a = LXMF.LXMRouter(identity=id_a, storagepath=store)
dest_a = router_a.register_delivery_identity(id_a, display_name="alice")
client_a = Client(router=router_a, destination=dest_a, nick="alice")

ch_a = client_a.join("#test")

deadline = time.time() + 15
while time.time() < deadline:
    if os.path.exists(os.path.join(shared, "b_ready.json")):
        break
    time.sleep(0.2)

time.sleep(2)
ch_a.send("hello from alice")
time.sleep(5)
""")


def test_two_client_channel_messaging(rnsd_instance, shared_dir):
    """Both clients join #test, Client A sends, Client B receives."""
    proc_b = run_client(_SCRIPT_B_MSG, shared_dir, "b", rnsd_instance)
    time.sleep(1)
    proc_a = run_client(_SCRIPT_A_MSG, shared_dir, "a", rnsd_instance)

    wait_and_check(proc_a, "client_a", timeout=40)
    wait_and_check(proc_b, "client_b", timeout=40)

    result = poll_for_file(os.path.join(shared_dir, "b_result.json"))
    assert len(result["received"]) > 0, "Client B did not receive any messages"
    assert result["received"][0]["body"] == "hello from alice"
    assert result["received"][0]["nick"] == "alice"


# ------------------------------------------------------------------
# Test 10.3: Join visibility
# ------------------------------------------------------------------

_SCRIPT_A_JOIN = _PREAMBLE + textwrap.dedent("""\
id_a = RNS.Identity()
router_a = LXMF.LXMRouter(identity=id_a, storagepath=store)
dest_a = router_a.register_delivery_identity(id_a, display_name="alice")
client_a = Client(router=router_a, destination=dest_a, nick="alice")

join_events = []

@client_a.on_join
def on_join(channel, nick):
    if nick != "alice":
        join_events.append(nick)

ch_a = client_a.join("#jointest")

with open(os.path.join(shared, "a_ready.json"), "w") as f:
    json.dump({"ready": True}, f)

deadline = time.time() + 25
while not join_events and time.time() < deadline:
    time.sleep(0.1)

with open(os.path.join(shared, "a_result.json"), "w") as f:
    json.dump({"join_events": join_events}, f)
""")

_SCRIPT_B_JOIN = _PREAMBLE + textwrap.dedent("""\
id_b = RNS.Identity()
router_b = LXMF.LXMRouter(identity=id_b, storagepath=store)
dest_b = router_b.register_delivery_identity(id_b, display_name="bob")
client_b = Client(router=router_b, destination=dest_b, nick="bob")

deadline = time.time() + 15
while time.time() < deadline:
    if os.path.exists(os.path.join(shared, "a_ready.json")):
        break
    time.sleep(0.2)

time.sleep(2)
ch_b = client_b.join("#jointest")
time.sleep(5)
""")


def test_join_visibility(rnsd_instance, shared_dir):
    """Client A joins first, Client B joins, A sees B's join event."""
    proc_a = run_client(_SCRIPT_A_JOIN, shared_dir, "a", rnsd_instance)
    time.sleep(1)
    proc_b = run_client(_SCRIPT_B_JOIN, shared_dir, "b", rnsd_instance)

    wait_and_check(proc_b, "client_b", timeout=40)
    wait_and_check(proc_a, "client_a", timeout=40)

    result = poll_for_file(os.path.join(shared_dir, "a_result.json"))
    assert "bob" in result["join_events"], \
        f"Client A did not see bob's join. Events: {result['join_events']}"


# ------------------------------------------------------------------
# Test 10.4: Nick change propagation
# ------------------------------------------------------------------

_SCRIPT_B_NICK = _PREAMBLE + textwrap.dedent("""\
id_b = RNS.Identity()
router_b = LXMF.LXMRouter(identity=id_b, storagepath=store)
dest_b = router_b.register_delivery_identity(id_b, display_name="bob")
client_b = Client(router=router_b, destination=dest_b, nick="bob")

nick_events = []
client_b.events.on("nick", lambda old, new: nick_events.append({"old": old, "new": new}))

ch_b = client_b.join("#nicktest")

with open(os.path.join(shared, "b_ready.json"), "w") as f:
    json.dump({"ready": True}, f)

deadline = time.time() + 25
while not nick_events and time.time() < deadline:
    time.sleep(0.1)

with open(os.path.join(shared, "b_result.json"), "w") as f:
    json.dump({"nick_events": nick_events}, f)
""")

_SCRIPT_A_NICK = _PREAMBLE + textwrap.dedent("""\
id_a = RNS.Identity()
router_a = LXMF.LXMRouter(identity=id_a, storagepath=store)
dest_a = router_a.register_delivery_identity(id_a, display_name="alice")
client_a = Client(router=router_a, destination=dest_a, nick="alice")

ch_a = client_a.join("#nicktest")

deadline = time.time() + 15
while time.time() < deadline:
    if os.path.exists(os.path.join(shared, "b_ready.json")):
        break
    time.sleep(0.2)

time.sleep(2)
client_a.change_nick("alice2")
time.sleep(5)
""")


def test_nick_change_propagation(rnsd_instance, shared_dir):
    """Client A changes nick, Client B sees the nick stanza."""
    proc_b = run_client(_SCRIPT_B_NICK, shared_dir, "b", rnsd_instance)
    time.sleep(1)
    proc_a = run_client(_SCRIPT_A_NICK, shared_dir, "a", rnsd_instance)

    wait_and_check(proc_a, "client_a", timeout=40)
    wait_and_check(proc_b, "client_b", timeout=40)

    result = poll_for_file(os.path.join(shared_dir, "b_result.json"))
    assert len(result["nick_events"]) > 0, "Client B did not receive nick change"
    assert result["nick_events"][0]["new"] == "alice2"


# ------------------------------------------------------------------
# Test 10.5: Direct messaging (privmsg)
# ------------------------------------------------------------------

_SCRIPT_B_DM = _PREAMBLE + textwrap.dedent("""\
id_b = RNS.Identity()
router_b = LXMF.LXMRouter(identity=id_b, storagepath=store)
dest_b = router_b.register_delivery_identity(id_b, display_name="bob")
client_b = Client(router=router_b, destination=dest_b, nick="bob")

dm_received = []

@client_b.on_privmsg
def on_dm(source_hash, msg):
    dm_received.append({"body": msg.body, "nick": msg.nick})

with open(os.path.join(shared, "b_info.json"), "w") as f:
    json.dump({"ready": True, "dest_hash": dest_b.hash.hex()}, f)

router_b.announce(dest_b.hash)

deadline = time.time() + 30
while not dm_received and time.time() < deadline:
    time.sleep(0.1)

with open(os.path.join(shared, "b_result.json"), "w") as f:
    json.dump({"dm_received": dm_received}, f)
""")

_SCRIPT_A_DM = _PREAMBLE + textwrap.dedent("""\
id_a = RNS.Identity()
router_a = LXMF.LXMRouter(identity=id_a, storagepath=store)
dest_a = router_a.register_delivery_identity(id_a, display_name="alice")
client_a = Client(router=router_a, destination=dest_a, nick="alice")

deadline = time.time() + 15
b_info = None
while time.time() < deadline:
    info_path = os.path.join(shared, "b_info.json")
    if os.path.exists(info_path):
        with open(info_path) as f:
            b_info = json.load(f)
        break
    time.sleep(0.2)

if not b_info:
    raise RuntimeError("Client B info not found")

dest_hash = bytes.fromhex(b_info["dest_hash"])

RNS.Transport.request_path(dest_hash)
path_deadline = time.time() + 15
while not RNS.Transport.has_path(dest_hash) and time.time() < path_deadline:
    time.sleep(0.2)

time.sleep(1)
client_a.privmsg(dest_hash, "secret message")
time.sleep(5)
""")


def test_direct_messaging(rnsd_instance, shared_dir):
    """Client A sends privmsg to Client B's destination hash."""
    proc_b = run_client(_SCRIPT_B_DM, shared_dir, "b", rnsd_instance)
    time.sleep(1)
    proc_a = run_client(_SCRIPT_A_DM, shared_dir, "a", rnsd_instance)

    wait_and_check(proc_a, "client_a", timeout=45)
    wait_and_check(proc_b, "client_b", timeout=45)

    result = poll_for_file(os.path.join(shared_dir, "b_result.json"))
    assert len(result["dm_received"]) > 0, "Client B did not receive the direct message"
    assert result["dm_received"][0]["body"] == "secret message"
    assert result["dm_received"][0]["nick"] == "alice"
