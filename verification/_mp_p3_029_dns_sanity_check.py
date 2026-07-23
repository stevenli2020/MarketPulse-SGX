"""Sanity check: is DNS resolution broken in general, or specifically for gateway.mas.gov.sg?"""
import socket

for host in ["google.com", "api-production.data.gov.sg", "eservices.mas.gov.sg", "www.mas.gov.sg", "gateway.mas.gov.sg"]:
    try:
        ip = socket.gethostbyname(host)
        print(f"{host} -> resolves to {ip}")
    except socket.gaierror as e:
        print(f"{host} -> DOES NOT RESOLVE: {e}")
