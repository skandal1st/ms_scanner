"""Stream exact RUNNING images over two SSH channels; no local binary archive.

Default is a plan. --execute imports/tags images on the new host only.
Abort if a source container changed since the inventory; refresh inventory first.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, default=Path(__file__).with_name('inventory-20261029.json'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    data = json.loads(args.inventory.read_text(encoding='utf-8'))
    source, target = data['source']['ssh'], data['target']['ssh']
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    for item in data['images']:
        actual = subprocess.check_output(ssh + [source, f"docker inspect --format '{{{{.Image}}}}' {item['container']}"], text=True).strip()
        if actual != item['id']:
            raise SystemExit(f"Inventory changed: {item['container']}; refresh image IDs before copying.")
    print(f"Verified {len(data['images'])} images: {source} -> {target}", flush=True)
    if not args.execute:
        return
    ids = list(dict.fromkeys(item['id'] for item in data['images']))
    sender = subprocess.Popen(ssh + [source, 'docker image save ' + ' '.join(ids)], stdout=subprocess.PIPE)
    try:
        receiver = subprocess.Popen(ssh + [target, 'sudo -n docker image load'], stdin=sender.stdout)
        sender.stdout.close()
        received = receiver.wait()
        sent = sender.wait()
        if sent or received:
            raise SystemExit(f"Image transfer failed: source={sent}, target={received}")
    finally:
        if sender.poll() is None:
            sender.terminate()
            sender.wait()
    for item in data['images']:
        subprocess.run(ssh + [target, f"sudo -n docker image tag {item['id']} {item['target_tag']}"], check=True)
        actual = subprocess.check_output(ssh + [target, f"sudo -n docker image inspect --format '{{{{.Id}}}}' {item['target_tag']}"], text=True).strip()
        if actual != item['id']:
            raise SystemExit('Loaded image digest does not match inventory')
    print('Images loaded and verified. No applications were started.')


if __name__ == '__main__':
    main()
