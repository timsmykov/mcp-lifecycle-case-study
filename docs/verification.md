# Verification

Run `python3 -m unittest -v`. The suite includes four focused source tests and one actual local HTTP/Unix-socket regression.

The fixture starts one temporary local server in a temporary directory, creates two clients, drops the response to an executed write, and checks that the write count does not increase through an automatic replay. No remote hosts, real credentials or model calls are used. This is transport-level evidence, not business workflow or full upstream protocol acceptance.
