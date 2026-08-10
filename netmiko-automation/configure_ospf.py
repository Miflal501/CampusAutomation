import os
import re
import sys
import yaml
import logging
from datetime import datetime
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoTimeoutException,
    NetmikoAuthenticationException,
)

INVENTORY_FILE = "devices.yaml"


# ===================================================================
# Setup: logging + inventory loading (shared by every stage)
# ===================================================================

def setup_logging():
    """
    Create logs/ if missing, and configure a timestamped log file
    so every run leaves its own record (required for MOP evidence
    and for demonstrating idempotency across multiple runs).
    """
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = f"logs/configure_routers_{timestamp}.log"

    logger = logging.getLogger("configure_routers")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logging to {log_path}")
    return logger


def load_inventory(path):
    """Load devices.yaml. Raises on missing/malformed file."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


def connect(device, logger):
    """
    Open a Netmiko SSH session to a device's management IP (VLAN 99 /
    loopback) and drop into enable mode. Returns the connection object,
    or None if the connection failed - callers must check for this.
    """
    conn_params = {
        "device_type": device["device_type"],
        "host": device["ip"],          # management IP, never data-plane IP
        "username": device["username"],
        "password": device["password"],
        "secret": device.get("secret", ""),
        "timeout": 10,
    }

    try:
        conn = ConnectHandler(**conn_params)
        conn.enable()
        logger.info(f"[{device['hostname']}] Connected via {device['ip']}")
        return conn

    except NetmikoAuthenticationException:
        logger.error(f"[{device['hostname']}] Authentication failed - check username/password/secret")
    except NetmikoTimeoutException:
        logger.error(f"[{device['hostname']}] Connection timed out - check IP/reachability")
    except Exception as e:
        logger.error(f"[{device['hostname']}] Unexpected connection error: {e}")

    return None


def configure_ospf(device, conn, logger):
    """
    Ensure every network statement in device['ospf_networks'] is present
    under 'router ospf <process>', and (if requested) that
    'default-information originate' is present.

    Assumes the OSPF process itself is already running (per project
    status) - this function only adds what's missing, it never
    recreates or removes the process.

    Idempotency approach:
        Read the current OSPF section with
        'show run | section ^router ospf' and check each desired line
        against it as plain text before pushing. Already-present lines
        are skipped; only missing ones are sent.
    """
    hostname = device["hostname"]
    process = device.get("ospf_process")
    networks = device.get("ospf_networks", [])

    if not process or not networks:
        logger.warning(f"[{hostname}] No 'ospf_process'/'ospf_networks' defined in inventory - skipping OSPF")
        return

    try:
        current_ospf_config = conn.send_command("show run | section ^router ospf")

        commands_to_send = []

        # --- Check each network statement --------------------------
        for net in networks:
            line = f"network {net['network']} {net['wildcard']} area {net['area']}"
            if line in current_ospf_config:
                logger.info(f"[{hostname}] OSPF: '{line}' already present - skipping (idempotent)")
            else:
                commands_to_send.append(line)

        # --- Check default-information originate --------------------
        if device.get("default_route_originate"):
            dio_line = "default-information originate"
            if dio_line in current_ospf_config:
                logger.info(f"[{hostname}] OSPF: '{dio_line}' already present - skipping (idempotent)")
            else:
                commands_to_send.append(dio_line)

        # --- Push only what's missing --------------------------------
        if commands_to_send:
            full_command_set = [f"router ospf {process}"] + commands_to_send
            output = conn.send_config_set(full_command_set)
            logger.info(f"[{hostname}] OSPF updated: {commands_to_send}")
            logger.debug(output)
        else:
            logger.info(f"[{hostname}] OSPF already fully configured - no changes made")

    except Exception as e:
        logger.error(f"[{hostname}] Failed to configure OSPF: {e}")

# ===================================================================
# Main
# ===================================================================

def main():
    logger = setup_logging()
    logger.info("=== EE8203 Router Automation Started ===")

    try:
        inventory = load_inventory(INVENTORY_FILE)
    except FileNotFoundError:
        logger.error(f"Inventory file '{INVENTORY_FILE}' not found")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse '{INVENTORY_FILE}': {e}")
        sys.exit(1)

    routers = inventory.get("routers", [])
    if not routers:
        logger.error("No routers found in inventory - nothing to do")
        sys.exit(1)

    for device in routers:
        conn = connect(device, logger)
        if conn is None:
            continue  # move on to next router rather than crashing the whole run

        try:
            configure_ospf(device, conn, logger)

        finally:
            conn.disconnect()
            logger.info(f"[{device['hostname']}] Disconnected")

    logger.info("=== EE8203 Router Automation Finished ===")


if __name__ == "__main__":
    main()
