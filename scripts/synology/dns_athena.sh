#!/usr/bin/env sh
# acme.sh DNS adapter. Public challenge data only; the Python helper reads the
# protected credential itself and performs HTTPS without secret process args.
dns_athena_add() {
  /usr/bin/python3 /volume1/@athena-acme/athena-acme.py dns-add "$1" "$2"
}

dns_athena_rm() {
  /usr/bin/python3 /volume1/@athena-acme/athena-acme.py dns-remove "$1" "$2"
}
