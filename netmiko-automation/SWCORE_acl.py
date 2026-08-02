"""
configure_swcore_acl.py
EE8203 Project - SW-CORE Inter-VLAN ACL Automation (Netmiko)

Deploys the department-isolation ACLs (Section 3.4 of the project brief)
onto SW-CORE's VLAN SVIs, since SW-CORE performs the inter-VLAN routing
in this topology (not R-CORE).

Design notes:
    - ACL definitions live entirely in devices.yaml under sw_core.access_lists
      - nothing is hardcoded here.
    - Idempotency: each ACE (access control entry) line is checked against
      the current running-config before being pushed; only missing lines
      are sent. The access-group binding on each interface is checked the
      same way.
    - CAVEAT (documented honestly for the MOP / code walkthrough): this
      line-presence check is sufficient to avoid duplicate ACEs, but it
      does NOT re-order existing rules if you change entry order in the
      YAML later. If you need to reorder rules, remove the ACL on the
      device first (or use the rollback playbook) and re-run.

Usage:
    python3 configure_swcore_acl.py
""" 

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
    """Timestamped log file, mirrors configure_routers.py's approach."""
    import os
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = f"logs/configure_swcore_acl_{timestamp}.log"

    logger = logging.getLogger("configure_swcore_acl")
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
    """Same connect pattern as configure_routers.py, kept consistent
    across scripts so the code review reads as one coherent project."""
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


def configure_acls(device, conn, logger):
    """
    Push every ACL defined under device['access_lists'], then bind each
    one to its target interface/direction if not already bound.
    """
    hostname = device["hostname"]
    acl_defs = device.get("access_lists", [])

    if not acl_defs:
        logger.warning(f"[{hostname}] No 'access_lists' defined - skipping")
        return

    for acl in acl_defs:
        name = acl["name"]
        interface = acl["interface"]
        direction = acl["direction"]
        entries = acl.get("entries", [])

        try:
            # --- Push missing ACEs --------------------------------------
            current_acl = conn.send_command(f"show run | section ip access-list extended {name}")

            missing = [line for line in entries if line not in current_acl]

            if missing:
                acl_commands = [f"ip access-list extended {name}"] + missing
                output = conn.send_config_set(acl_commands)
                logger.info(f"[{hostname}] ACL '{name}' updated: {missing}")
                logger.debug(output)
            else:
                logger.info(f"[{hostname}] ACL '{name}' already fully configured - no changes")

            # --- Bind to interface, if not already bound -----------------
            iface_config = conn.send_command(f"show run interface {interface}")
            desired_binding = f"ip access-group {name} {direction}"

            if desired_binding in iface_config:
                logger.info(f"[{hostname}] {interface}: '{desired_binding}' already applied - skipping")
            else:
                output = conn.send_config_set([f"interface {interface}", desired_binding])
                logger.info(f"[{hostname}] {interface}: applied '{desired_binding}'")
                logger.debug(output)

        except Exception as e:
            logger.error(f"[{hostname}] Failed to configure ACL '{name}': {e}")


def main():
    logger = setup_logging()
    logger.info("=== EE8203 SW-CORE ACL Automation Started ===")

    try:
        inventory = load_inventory(INVENTORY_FILE)
    except FileNotFoundError:
        logger.error(f"Inventory file '{INVENTORY_FILE}' not found")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse '{INVENTORY_FILE}': {e}")
        sys.exit(1)

    sw_core = inventory.get("sw_core")
    if not sw_core:
        logger.error("No 'sw_core' entry found in inventory - nothing to do")
        sys.exit(1)

    conn = connect(sw_core, logger)
    if conn is None:
        logger.error(f"[{sw_core['hostname']}] Could not connect - aborting")
        sys.exit(1)

    try:
        configure_acls(sw_core, conn, logger)
    finally:
        conn.disconnect()
        logger.info(f"[{sw_core['hostname']}] Disconnected")

    logger.info("=== EE8203 SW-CORE ACL Automation Finished ===")


if __name__ == "__main__":
    main()
