#!/usr/bin/env python3
"""
configure_routers.py
EE8203 Project - Router Automation (Netmiko)
Faculty of Engineering, University of Ruhuna

Automates on R-CORE and R-EDGE:
    [DONE]  Interface IP addressing   -> configure_interfaces()
    [DONE]  OSPF configuration        -> configure_ospf()
    [NEXT]  NAT rules (R-EDGE only)   -> configure_nat()
    [NEXT]  ACL deployment            -> configure_acls()

Design notes:
    - All device/interface parameters are read from devices.yaml.
      Nothing device-specific is hardcoded in this file.
    - Every push function checks the device's current running-config
      FIRST and only sends commands for what is missing/different.
      This is what makes re-running the script idempotent - it will
      not create duplicate "ip address" lines or similar.
    - Every attempt (success or failure) is written to a timestamped
      log file under logs/, in addition to being printed to screen.

Usage:
    source venv/bin/activate
    python3 configure_routers.py
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


# ===================================================================
# Stage: Interface IP addressing (idempotent)
# ===================================================================

def configure_interfaces(device, conn, logger):
    """
    Push IP addressing to every interface listed under device['interfaces']
    in devices.yaml.

    Idempotency approach:
        Before pushing, read the device's current config for that specific
        interface (`show run interface <name>`) and check whether the
        desired "ip address <ip> <mask>" line is already present. If it
        is, skip pushing and log it as already-configured. This means
        running the script twice in a row produces zero additional
        changes on the second run.
    """
    hostname = device["hostname"]
    interfaces = device.get("interfaces", [])

    if not interfaces:
        logger.warning(f"[{hostname}] No 'interfaces' defined in inventory - skipping")
        return

    for iface in interfaces:
        name = iface["name"]
        ip = iface["ip"]
        mask = iface["mask"]
        description = iface.get("description", "")

        try:
            # --- Idempotency check ---------------------------------
            current_config = conn.send_command(f"show run interface {name}")
            desired_line = f"ip address {ip} {mask}"

            if desired_line in current_config:
                logger.info(f"[{hostname}] {name} already has {ip} {mask} - skipping (idempotent)")
                continue

            # --- Push config -----------------------------------------
            config_commands = [
                f"interface {name}",
                f"description {description}",
                f"ip address {ip} {mask}",
                "no shutdown",
            ]

            output = conn.send_config_set(config_commands)
            logger.info(f"[{hostname}] Configured {name} -> {ip} {mask}")
            logger.debug(output)

        except Exception as e:
            # Catch per-interface errors so one bad interface entry
            # doesn't abort the whole device's configuration run.
            logger.error(f"[{hostname}] Failed to configure {name}: {e}")


# ===================================================================
# Stage: OSPF configuration (idempotent)
# ===================================================================

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
# Stage: NAT overload configuration (idempotent, R-EDGE only)
# ===================================================================

def configure_nat(device, conn, logger):
    """
    Configure PAT (NAT overload) restricted to VLAN_DEIE and VLAN_DCEE,
    per project requirement 3.3: "only VLAN_DEIE and VLAN_DCEE are
    permitted internet egress."

    Only runs if the device has a 'nat' block in devices.yaml (i.e.
    R-EDGE). R-CORE has no 'nat' key, so this is a silent no-op for it.

    Idempotency approach (3 independent checks, each skipped if already
    present):
        1. Standard ACL permit lines for the two allowed subnets
        2. 'ip nat inside' / 'ip nat outside' on the two interfaces
        3. The 'ip nat inside source list ... overload' statement
    """
    hostname = device["hostname"]
    nat_cfg = device.get("nat")

    if not nat_cfg:
        logger.info(f"[{hostname}] No 'nat' block in inventory - not a NAT device, skipping")
        return

    acl_name = nat_cfg["acl_name"]
    inside_if = nat_cfg["inside_interface"]
    outside_if = nat_cfg["outside_interface"]
    networks = nat_cfg.get("permitted_networks", [])

    try:
        # --- 1. Standard ACL: permit only the allowed subnets ----------
        current_acl = conn.send_command(f"show run | section ip access-list standard {acl_name}")

        missing_lines = []
        for net in networks:
            line = f"permit {net['network']} {net['wildcard']}"
            if line in current_acl:
                logger.info(f"[{hostname}] NAT ACL: '{line}' already present - skipping (idempotent)")
            else:
                missing_lines.append(line)

        if missing_lines:
            acl_commands = [f"ip access-list standard {acl_name}"] + missing_lines
            output = conn.send_config_set(acl_commands)
            logger.info(f"[{hostname}] NAT ACL '{acl_name}' updated: {missing_lines}")
            logger.debug(output)
        else:
            logger.info(f"[{hostname}] NAT ACL '{acl_name}' already fully configured")

        # --- 2. Inside/outside interface marking ------------------------
        for iface_name, direction in [(inside_if, "inside"), (outside_if, "outside")]:
            iface_config = conn.send_command(f"show run interface {iface_name}")
            desired = f"ip nat {direction}"
            if desired in iface_config:
                logger.info(f"[{hostname}] {iface_name}: '{desired}' already present - skipping (idempotent)")
            else:
                output = conn.send_config_set([f"interface {iface_name}", desired])
                logger.info(f"[{hostname}] {iface_name}: applied '{desired}'")
                logger.debug(output)

        # --- 3. The overload statement itself ----------------------------
        current_nat = conn.send_command("show run | include ip nat inside source")
        desired_nat_line = f"ip nat inside source list {acl_name} interface {outside_if} overload"

        if desired_nat_line in current_nat:
            logger.info(f"[{hostname}] NAT overload statement already present - skipping (idempotent)")
        else:
            output = conn.send_config_set([desired_nat_line])
            logger.info(f"[{hostname}] NAT overload configured: {desired_nat_line}")
            logger.debug(output)

    except Exception as e:
        logger.error(f"[{hostname}] Failed to configure NAT: {e}")

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
            configure_interfaces(device, conn, logger)
            configure_ospf(device, conn, logger)
            configure_nat(device, conn, logger)
            # configure_acls(device, conn, logger)

        finally:
            conn.disconnect()
            logger.info(f"[{device['hostname']}] Disconnected")

    logger.info("=== EE8203 Router Automation Finished ===")


if __name__ == "__main__":
    main()
