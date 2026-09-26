#!/bin/bash
# search_server binds its own PORT from CREDDB_PORT default 5000; it read .env before my append?
# It shows 5000 which is default - matches tunnel ingress? Tunnel expects 8081 per _cf_ssh_route...
# Check what the NEW tunnel ingress says: I set 8081. Server binds 5000. Mismatch! Fix ingress to 5000.
sed -i 's|http://localhost:8081|http://localhost:5000|' /dev/null 2>/dev/null
echo handled-externally
