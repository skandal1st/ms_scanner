"""Byte-preserving SSH-to-SSH stream. Never prints payload or creates local copies."""
import argparse
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--source-command', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--target-command', required=True)
    args = parser.parse_args()
    ssh = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10']
    sender = subprocess.Popen(ssh + [args.source,args.source_command], stdout=subprocess.PIPE)
    try:
        receiver = subprocess.Popen(ssh + [args.target,args.target_command], stdin=sender.stdout)
        sender.stdout.close()
        received, sent = receiver.wait(), sender.wait()
        if sent or received:
            raise SystemExit(f'Transfer failed: source={sent}, target={received}')
    finally:
        if sender.poll() is None:
            sender.terminate()
            sender.wait()
    print('Encrypted stream transferred successfully.')


if __name__ == '__main__':
    main()
