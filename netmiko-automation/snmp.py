"""
configure_snmp.py
EE8203 Project - SNMPv2c Automation (Netmiko)

Pushes the SNMPv2c community string and trap destination to EVERY
device in the inventory (routers, SW-CORE, and all distribution/access
switches), per requirement: "Automate SNMPv2c community string and
trap destination configuration on all devices."

Kept as its own script (rather than folded into configure_routers.py)
because it targets a different, broader device set - all routers AND
all switches - which would blur the scope of configure_routers.py.

Idempotency approach:
    Read 'show run | include snmp-server' once per device, then check
    each of the 4 desired lines against it individually before pushing.
"""

import os
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


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = f"logs/configure_snmp_{timestamp}.log"

    logger = logging.getLogger("configure_snmp")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info(f"Logging to {log_path}")
    return logger


def load_inventory(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def connect(device, logger):
    conn_params = {
        "device_type": device["device_type"],
        "host": device["ip"],
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
        logger.error(f"[{device['hostname']}] Authentication failed")
    except NetmikoTimeoutException:
        logger.error(f"[{device['hostname']}] Connection timed out")
    except Exception as e:
        logger.error(f"[{device['hostname']}] Unexpected connection error: {e}")
    return None


def configure_snmp(device, conn, logger, snmp_cfg):
    """
    Push the 4 standard SNMPv2c lines to a single device, skipping any
    that are already present.
    """
    hostname = device["hostname"]

    desired_lines = [
        f"snmp-server community {snmp_cfg['community']} RO",
        f"snmp-server location {snmp_cfg['location']}",
        f"snmp-server contact {snmp_cfg['contact']}",
        f"snmp-server host {snmp_cfg['trap_destination']} version 2c {snmp_cfg['trap_community']}",
    ]

    try:
        current_snmp_config = conn.send_command("show run | include snmp-server")

        missing = [line for line in desired_lines if line not in current_snmp_config]

        if missing:
            output = conn.send_config_set(missing)
            logger.info(f"[{hostname}] SNMP config applied: {missing}")
            logger.debug(output)
        else:
            logger.info(f"[{hostname}] SNMP already fully configured - no changes (idempotent)")

    except Exception as e:
        logger.error(f"[{hostname}] Failed to configure SNMP: {e}")


def main():
    logger = setup_logging()
    logger.info("=== EE8203 SNMP Automation Started (all devices) ===")

    try:
        inventory = load_inventory(INVENTORY_FILE)
    except FileNotFoundError:
        logger.error(f"Inventory file '{INVENTORY_FILE}' not found")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse '{INVENTORY_FILE}': {e}")
        sys.exit(1)

    snmp_cfg = inventory.get("snmp")
    if not snmp_cfg:
        logger.error("No 'snmp' block found in inventory - nothing to push")
        sys.exit(1)

    # Build the full device list: routers + sw_core + switches
    all_devices = []
    all_devices.extend(inventory.get("routers", []))
    if inventory.get("sw_core"):
        all_devices.append(inventory["sw_core"])
    all_devices.extend(inventory.get("switches", []))

    if not all_devices:
        logger.error("No devices found in inventory - nothing to do")
        sys.exit(1)

    for device in all_devices:
        conn = connect(device, logger)
        if conn is None:
            continue  # skip unreachable device, keep going with the rest

        try:
            configure_snmp(device, conn, logger, snmp_cfg)
        finally:
            conn.disconnect()
            logger.info(f"[{device['hostname']}] Disconnected")

    logger.info("=== EE8203 SNMP Automation Finished ===")


if __name__ == "__main__":
    main()
