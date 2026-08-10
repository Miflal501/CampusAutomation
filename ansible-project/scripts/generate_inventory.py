#!/usr/bin/env python3
"""
generate_inventory.py
EE8203 Project - single-source-of-truth inventory generator

Reads the SAME devices.yaml already used by ../netmiko-automation, pulls
out only the switches with role 'distribution' or 'access', and writes
inventory/hosts.yml grouped accordingly. This means IPs and credentials
are defined in exactly one place in the whole project (devices.yaml) -
Ansible never hardcodes them, matching the spirit of Section 4.1's
"no hardcoded device parameters" requirement across both automation tools.

Run this BEFORE every ansible-playbook invocation, or whenever
devices.yaml changes (new switch, changed password, etc.):

    python3 scripts/generate_inventory.py
    python3 scripts/generate_inventory.py --devices-file /path/to/devices.yaml

inventory/hosts.yml is a generated artefact - regenerate it, don't
hand-edit it (see the header comment written into the file).
"""

import argparse
import sys
import yaml
from pathlib import Path

# Only these devices.yaml switch roles are managed by this Ansible
# project. SW-CORE (role: core_switch) is out of scope for Section 4.2 -
# it stays a manually/Netmiko-managed device.
MANAGED_ROLES = {"distribution", "access", "core_distribution"}

DEFAULT_DEVICES_FILE = Path(__file__).resolve().parent.parent.parent / "netmiko-automation" / "devices.yaml"
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "inventory" / "hosts.yml"


def load_devices(devices_file: Path) -> dict:
    if not devices_file.exists():
        sys.exit(f"ERROR: devices file not found at {devices_file}")
    with open(devices_file) as f:
        return yaml.safe_load(f)


def build_inventory(inventory_data: dict) -> dict:
    switches = inventory_data.get("switches", [])

    groups = {role: {"hosts": {}} for role in MANAGED_ROLES}

    for sw in switches:
        role = sw.get("role")
        if role not in MANAGED_ROLES:
            continue  # e.g. SW-CORE (core_switch) - not managed here

        hostname = sw["hostname"]
        groups[role]["hosts"][hostname] = {
            "ansible_host": sw["ip"],
            "ansible_user": sw["username"],
            "ansible_password": sw["password"],
            "ansible_become": True,
            "ansible_become_password": sw.get("secret", sw["password"]),
        }

    return {
        "all": {
            "children": {
                **groups,
                "switches": {"children": {role: None for role in MANAGED_ROLES}},
            }
        }
    }


def render_yaml(inventory: dict) -> str:
    header = (
        "# ============================================================\n"
        "# GENERATED FILE - do not hand-edit.\n"
        "# Source of truth: devices.yaml (shared with netmiko-automation)\n"
        "# Regenerate with: python3 scripts/generate_inventory.py\n"
        "# ============================================================\n"
    )
    body = yaml.dump(inventory, default_flow_style=False, sort_keys=False)
    # yaml.dump renders {"switches": {"children": {"distribution": None, ...}}}
    # as "distribution: null" - null is valid YAML for an empty mapping
    # here and ansible-inventory reads it fine, but tidy it up for humans:
    body = body.replace(": null", ":")
    return header + "---\n" + body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--devices-file",
        type=Path,
        default=DEFAULT_DEVICES_FILE,
        help=f"Path to devices.yaml (default: {DEFAULT_DEVICES_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_FILE,
        help=f"Path to write inventory (default: {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    inventory_data = load_devices(args.devices_file)
    inventory = build_inventory(inventory_data)

    managed_count = sum(len(inventory["all"]["children"][r]["hosts"]) for r in MANAGED_ROLES)
    if managed_count == 0:
        sys.exit(
            "ERROR: no switches with role 'distribution' or 'access' found in "
            f"{args.devices_file} - check the 'role' field on each switch entry."
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_yaml(inventory))

    print(f"Wrote {args.output} from {args.devices_file}")
    for role in MANAGED_ROLES:
        hosts = list(inventory["all"]["children"][role]["hosts"].keys())
        print(f"  {role}: {hosts}")


if __name__ == "__main__":
    main()
