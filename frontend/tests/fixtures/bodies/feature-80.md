Check why would that happen:

2026-09-17T14:58:26.419078Z [error    ] Fatal error in message reader: Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes...

and it will cause the session stop, we must resume it by ourself.

this session was working: a3caae24-0a15-4cca-8735-03b9b3dbc20a