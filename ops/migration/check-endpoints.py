"""Read-only HTTPS checks, with original hostnames/SNI and a chosen server IP."""
import argparse
import ipaddress
import shutil
import subprocess

ENDPOINTS = [('skandata.ru', '/health'), ('sverka.skandata.ru', '/healthz'),
             ('otgruzka.skandata.ru', '/healthz')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ip', required=True, type=ipaddress.ip_address)
    args = parser.parse_args()
    curl = shutil.which('curl')
    if not curl:
        parser.error('curl is required')
    address = str(args.ip)
    if args.ip.version == 6:
        address = f'[{address}]'
    failed = False
    for host, route in ENDPOINTS:
        result = subprocess.run([curl, '--silent', '--show-error', '--fail',
            '--noproxy', '*', '--connect-timeout', '5', '--max-time', '15',
            '--resolve', f'{host}:443:{address}', '--output',
            'NUL' if __import__('os').name == 'nt' else '/dev/null',
            '--write-out', f'{host} HTTP %{{http_code}} (%{{time_total}}s)\n',
            f'https://{host}{route}'], check=False)
        failed |= result.returncode != 0
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
