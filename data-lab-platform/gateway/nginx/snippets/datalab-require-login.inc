# Include inside a location to require a Label Studio sign-in.
# Requests from the host / compose network (local ops scripts, health probes)
# stay allowed; LAN clients must be signed in. Collection-station endpoints
# (datalab-stream-ingest.conf) do not include this: they use station tokens.
satisfy any;
allow 127.0.0.1;
allow 172.16.0.0/12;
deny all;
auth_request /_datalab_auth;
error_page 401 = @datalab_login;
