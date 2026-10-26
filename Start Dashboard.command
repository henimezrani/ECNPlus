#!/bin/zsh
# Double-click in Finder to start the dashboard; leave its Terminal window open.
cd -- "${0:A:h}" || exit 1
exec python3 -m ecnplus serve
